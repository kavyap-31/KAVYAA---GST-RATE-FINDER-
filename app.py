from __future__ import annotations
import streamlit as st
import sqlite3, hashlib, secrets, re, io, csv, json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, date
from pathlib import Path
import pandas as pd

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / 'gst_finder.db'
DISCLAIMER = ('GST FINDER IS AN EDUCATIONAL TOOL, NOT AN OFFICIAL GOVERNMENT PLATFORM OR SUBSTITUTE FOR PROFESSIONAL TAX ADVICE. '
             'VERIFY ALL GST RATES, CLASSIFICATIONS AND VALUATION METHODS AGAINST CURRENT OFFICIAL NOTIFICATIONS BEFORE MAKING TAX OR BUSINESS DECISIONS.')
CBIC_URL = 'https://cbic-gst.gov.in/hindi/gst-goods-services-rates.html'
COUNCIL_URL = 'https://www.gstcouncil.gov.in/cgst-rate-notification'

# These records are a small, transparent starter index, not a complete tariff database.
# Rate entries below reflect the CBIC schedule currently surfaced at the official URL;
# every entry still carries a review warning because classification facts can change the result.
SEED = [
 {'name':'Fresh milk','description':'Fresh milk; not concentrated and without added sugar or sweetener','category':'Dairy','hsn':'0401','rate':'0','aliases':'milk;liquid milk;fresh cow milk;fresh buffalo milk','status':'Official schedule entry; verify product facts','source':CBIC_URL,'effective':'See applicable current notification'},
 {'name':'Pasteurised milk','description':'Pasteurised milk; not concentrated and without added sugar or sweetener','category':'Dairy','hsn':'0401','rate':'0','aliases':'pasteurized milk;toned milk;double toned milk;full cream milk','status':'Official schedule entry; verify product facts','source':CBIC_URL,'effective':'See applicable current notification'},
 {'name':'UHT milk','description':'Ultra High Temperature (UHT) milk','category':'Dairy','hsn':'0401','rate':'5','aliases':'long life milk;tetra pack milk;long-life milk','status':'Official schedule entry; verify product facts','source':CBIC_URL,'effective':'Verify notification effective date'},
 {'name':'Milk powder','description':'Milk and cream, concentrated or containing added sugar/sweetener, including skimmed milk powder; classification exceptions may apply','category':'Dairy','hsn':'0402','rate':'5','aliases':'skimmed milk powder;smp;dairy whitener;milk food','status':'Official schedule entry; verify exact product','source':CBIC_URL,'effective':'Verify notification effective date'},
 {'name':'Condensed milk','description':'Condensed milk; verify composition and exact tariff entry','category':'Dairy','hsn':'0402','rate':'5','aliases':'sweetened condensed milk;unsweetened condensed milk','status':'Provisional match; verify current notification','source':COUNCIL_URL,'effective':'Verify notification effective date'},
 {'name':'Curd / dahi (pre-packaged and labelled)','description':'Curd, lassi and buttermilk when pre-packaged and labelled','category':'Dairy','hsn':'0403','rate':'5','aliases':'dahi;yogurt yoghurt;lassi;buttermilk','status':'Official schedule entry; packaging conditions matter','source':CBIC_URL,'effective':'Verify notification effective date'},
 {'name':'Curd / dahi (other than pre-packaged and labelled)','description':'Curd, lassi and buttermilk other than pre-packaged and labelled','category':'Dairy','hsn':'0403','rate':'0','aliases':'loose curd;loose dahi;unpackaged curd','status':'Official schedule entry; packaging conditions matter','source':CBIC_URL,'effective':'Verify notification effective date'},
 {'name':'Paneer (pre-packaged and labelled)','description':'Chena or paneer pre-packaged and labelled','category':'Dairy','hsn':'0406','rate':'5','aliases':'chenna;packaged paneer','status':'Official schedule entry; packaging conditions matter','source':CBIC_URL,'effective':'Verify notification effective date'},
 {'name':'Paneer (other than pre-packaged and labelled)','description':'Chena or paneer other than pre-packaged and labelled','category':'Dairy','hsn':'0406','rate':'0','aliases':'loose paneer;unpackaged paneer','status':'Official schedule entry; packaging conditions matter','source':CBIC_URL,'effective':'Verify notification effective date'},
 {'name':'Milk beverage','description':'Beverages containing milk; verify formulation and tariff classification','category':'Beverages','hsn':'22029930','rate':'12','aliases':'milk drink;flavoured milk;flavored milk','status':'Provisional match; formulation must be checked','source':CBIC_URL,'effective':'Verify notification effective date'},
 {'name':'Soya milk drink','description':'Soya milk drinks','category':'Beverages','hsn':'22029910','rate':'12','aliases':'soy milk;soya drink;soy beverage','status':'Official schedule entry; verify formulation','source':CBIC_URL,'effective':'Verify notification effective date'},
]

st.set_page_config(page_title='GST FINDER', page_icon='🧾', layout='wide', initial_sidebar_state='expanded')


def db():
    con=sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory=sqlite3.Row
    return con

def init_db():
    """Create schema and idempotently load bundled product records."""
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT, category TEXT, hsn TEXT, rate TEXT, aliases TEXT, status TEXT, source TEXT, effective TEXT, manufacturer TEXT DEFAULT '', active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL, query TEXT, result TEXT, created_at TEXT NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE INDEX IF NOT EXISTS idx_products_hsn ON products(hsn);
        CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
        CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, created_at);
        """)
        # Always restore starter records, even if an older database already has rows.
        for item in SEED:
            exists = c.execute('SELECT id FROM products WHERE lower(name)=lower(?) AND hsn=? LIMIT 1',
                               (item['name'], item['hsn'])).fetchone()
            if exists:
                c.execute("""UPDATE products SET description=?, category=?, rate=?, aliases=?,
                             status=?, source=?, effective=?, active=1 WHERE id=?""",
                          (item['description'], item['category'], item['rate'], item['aliases'],
                           item['status'], item['source'], item['effective'], exists['id']))
            else:
                c.execute('INSERT INTO products(name,description,category,hsn,rate,aliases,status,source,effective) VALUES(:name,:description,:category,:hsn,:rate,:aliases,:status,:source,:effective)', item)

        # Import the supplied CBIC 01-04-2023 schedule as HISTORICAL search material.
        # Idempotent import: it never overwrites the starter records above.
        historical = APP_DIR / 'gst_goods_historical_2023.csv'
        if historical.exists():
            frame = pd.read_csv(historical, dtype=str).fillna('')
            for _, row in frame.iterrows():
                name, hsn = str(row.get('name', '')).strip(), str(row.get('hsn', '')).strip()
                desc = str(row.get('description', '')).strip()
                if not name or not hsn:
                    continue
                duplicate = c.execute('SELECT 1 FROM products WHERE lower(name)=lower(?) AND hsn=? AND lower(description)=lower(?) LIMIT 1',
                                      (name, hsn, desc)).fetchone()
                if not duplicate:
                    c.execute('INSERT INTO products(name,description,category,hsn,rate,aliases,status,source,effective,manufacturer) VALUES(?,?,?,?,?,?,?,?,?,?)',
                              tuple(str(row.get(k, '') or '') for k in ['name','description','category','hsn','rate','aliases','status','source','effective','manufacturer']))

def norm(s):
    return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()

def save_history(kind, query, result):
    uid=st.session_state.get('user_id')
    if uid is None: return
    with db() as c:
        c.execute('INSERT INTO history(user_id,kind,query,result,created_at) VALUES(?,?,?,?,?)',(uid,kind,query,result,datetime.now().isoformat(timespec='seconds')))

def search_products(term, hsn='', category='All categories', manufacturer=''):
    with db() as c:
        rows = [dict(r) for r in c.execute('SELECT * FROM products WHERE active=1').fetchall()]
    q = norm(term)
    h = re.sub(r'\D', '', str(hsn or ''))
    matches = []
    for p in rows:
        name = norm(p.get('name', ''))
        desc = norm(p.get('description', ''))
        cat = norm(p.get('category', ''))
        maker = norm(p.get('manufacturer', ''))
        aliases = [norm(x) for x in str(p.get('aliases') or '').split(';') if norm(x)]
        hay = ' '.join([name, desc, cat, maker, *aliases])
        score = 0
        if q:
            if name == q: score += 200
            if q in name: score += 150
            if q in desc: score += 100
            if q in cat: score += 60
            if q in aliases: score += 180
            elif any(q in a or a in q for a in aliases): score += 90
            tokens = q.split()
            if tokens and all(token in hay for token in tokens): score += 75
            if tokens and any(token in name or token in desc or token in ' '.join(aliases) for token in tokens): score += 25
        if h:
            stored_codes = re.findall(r'\d{2,8}', str(p.get('hsn') or ''))
            if h in stored_codes: score += 250
            elif any(code.startswith(h) or h.startswith(code) for code in stored_codes): score += 120
            else: continue
        if category != 'All categories' and str(p.get('category') or '') != category:
            continue
        if manufacturer and norm(manufacturer) not in maker:
            continue
        if (q or h) and score > 0:
            matches.append((score, p))
    matches.sort(key=lambda item: (-item[0], norm(item[1].get('name', '')), item[1].get('hsn', '')))
    return [p for _, p in matches]

def money(v):
    try: return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation,ValueError): raise ValueError('Enter a valid amount.')

def fmt(v): return f'₹{money(v):,.2f}'

def tax_parts(taxable, rate, intra):
    base=money(taxable); r=Decimal(str(rate)); tax=(base*r/Decimal('100')).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
    if intra: return tax, (tax/2).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP), (tax-tax/2).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP), Decimal('0.00')
    return tax, Decimal('0.00'), Decimal('0.00'), tax

def password_hash(password,salt): return hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),310000).hex()

def auth_panel():
    st.sidebar.subheader('Account')
    mode=st.sidebar.radio('Access',['Guest','Sign in','Register'], horizontal=False)
    if mode=='Guest':
        if st.sidebar.button('Continue as guest',use_container_width=True):
            st.session_state.user_id=None; st.session_state.username='Guest'; st.session_state.authenticated=True
        return
    with st.sidebar.form('auth_form'):
        username=st.text_input('Username').strip()
        password=st.text_input('Password',type='password')
        confirm=st.text_input('Confirm password',type='password') if mode=='Register' else None
        submitted=st.form_submit_button(mode, use_container_width=True)
    if submitted:
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,32}',username): st.sidebar.error('Username must be 3–32 characters: letters, numbers, dot, underscore or hyphen.')
        elif len(password)<10: st.sidebar.error('Use a password of at least 10 characters.')
        elif mode=='Register' and password!=confirm: st.sidebar.error('Passwords do not match.')
        elif mode=='Register':
            salt=secrets.token_hex(16)
            try:
                with db() as c: c.execute('INSERT INTO users(username,salt,password_hash,created_at) VALUES(?,?,?,?)',(username,salt,password_hash(password,salt),datetime.now().isoformat()))
                st.session_state.user_id=None; st.session_state.username=username; st.session_state.authenticated=True
                st.sidebar.success('Account created. Sign in to enable saved history.')
            except sqlite3.IntegrityError: st.sidebar.error('That username already exists.')
        else:
            with db() as c: user=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
            if user and secrets.compare_digest(user['password_hash'],password_hash(password,user['salt'])):
                st.session_state.user_id=user['id']; st.session_state.username=username; st.session_state.authenticated=True
                st.sidebar.success('Signed in.')
            else: st.sidebar.error('Incorrect username or password.')

def product_result(p):
    rate=Decimal(str(p['rate']))
    st.markdown(f"### {p['name']}")
    c1,c2,c3=st.columns(3)
    c1.metric('GST rate',f'{rate}%')
    c2.metric('CGST (intra-state)',f'{rate/2}%')
    c3.metric('SGST (intra-state)',f'{rate/2}%')
    st.write('**Description:**',p['description'])
    st.write('**Category:**',p['category'],' | **HSN:**',p['hsn'])
    st.write('**Verification:**',p['status'])
    st.write('**Effective date:**',p['effective'])
    st.write('**Source:**',p['source'])
    st.caption('A matching database record is not a legal classification opinion. Check product composition, packaging, labelling, intended use and the applicable notification.')

def home_page():
    st.title('GST RATE FINDER')
    st.caption('Search product descriptions, aliases, categories and HSN codes.')
    with st.container(border=True):
        st.subheader('1. Find a product')
        c1,c2=st.columns([3,1])
        term=c1.text_input('Product name or description',placeholder='Example: milk, shampoo, rice, mobile phone')
        hsn=c2.text_input('Optional HSN code',placeholder='Example: 0401')
        c3,c4=st.columns(2)
        with db() as con: cats=['All categories']+sorted({r[0] for r in con.execute("SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND category!=''")})
        category=c3.selectbox('Category',cats)
        manufacturer=c4.text_input('Manufacturer (optional)')
        clicked=st.button('SEARCH GST RATE',type='primary',use_container_width=True)
    if clicked:
        if not term.strip() and not hsn.strip(): st.warning('Enter a product name or HSN code to search.')
        else:
            results=search_products(term,hsn,category,manufacturer)
            save_history('Product search',f'{term} | HSN {hsn}',f'{len(results)} results')
            if not results:
                st.error('No matching record in the currently loaded database. Try a shorter product name, an alternative name, or an HSN heading. This does not mean the product has no GST entry.')
                st.info('The starter database is not exhaustive. Import an authorised, reviewed product CSV from the database section on the INFO page after verifying each row.')
            else:
                st.success(f'{len(results)} matching record(s) found.')
                for i,p in enumerate(results):
                    with st.expander(f"{p['name']}  |  HSN {p['hsn']}  |  {p['rate']}% GST",expanded=(i==0)):
                        product_result(p)
    st.divider()
    st.subheader('Popular searches')
    popular=['Milk','Rice','Shampoo','Toothpaste','Mobile phone','Cement','Paneer','Curd']
    cols=st.columns(4)
    for i,x in enumerate(popular):
        if cols[i%4].button(x,key='popular_'+x,use_container_width=True):
            st.session_state.pending_search=x; st.rerun()
    if st.session_state.get('pending_search'):
        q=st.session_state.pop('pending_search'); results=search_products(q)
        st.subheader(f'Search results: {q}')
        if results:
            for p in results:
                with st.expander(f"{p['name']} — HSN {p['hsn']} — {p['rate']}%",expanded=True): product_result(p)
        else: st.warning('No match in the currently loaded database. Try another term or HSN.')

def calculator_page():
    st.title('GST CALCULATOR & BILL PREVIEW')
    st.caption('Calculations use Decimal arithmetic and round tax to two decimal places.')
    with st.form('calc_form'):
        a,b,c=st.columns(3)
        amount=a.text_input('Amount (₹)','1000')
        rate=b.selectbox('GST rate (%)',['0','0.1','0.25','1.5','3','5','12','18','28','40'],index=6)
        inclusive=c.selectbox('Amount type',['Exclusive of GST','Inclusive of GST'])
        d,e=st.columns(2)
        intra=d.radio('Supply type',['Within same state (CGST + SGST)','Inter-state (IGST)'])
        cess=e.text_input('Cess rate (%)','0')
        item=st.text_input('Item description','Product / service')
        hsn=st.text_input('HSN / SAC (optional)')
        submit=st.form_submit_button('CALCULATE GST',type='primary',use_container_width=True)
    if submit:
        try:
            gross=money(amount); r=Decimal(rate); cr=Decimal(cess)
            if gross<0 or cr<0: raise ValueError('Amounts and rates cannot be negative.')
            if inclusive=='Inclusive of GST': taxable=(gross*100/(100+r+cr)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
            else: taxable=gross
            tax,cgst,sgst,igst=tax_parts(taxable,r,intra.startswith('Within'))
            cess_amt=(taxable*cr/100).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
            total=taxable+tax+cess_amt
            st.session_state.last_bill={'item':item,'hsn':hsn,'taxable':str(taxable),'rate':str(r),'cgst':str(cgst),'sgst':str(sgst),'igst':str(igst),'cess':str(cess_amt),'total':str(total),'method':'GST-inclusive extraction' if inclusive.startswith('Inclusive') else 'GST-exclusive calculation'}
            save_history('GST calculation',f'{item}; amount {amount}; rate {rate}%',f'Total {total}')
        except Exception as ex: st.error(str(ex))
    bill=st.session_state.get('last_bill')
    if bill:
        st.subheader('Bill preview')
        st.warning('This is a calculation preview, not necessarily a tax invoice. Verify invoice particulars and legal requirements before issuing.')
        st.write(f"**{bill['item']}** | HSN/SAC: {bill['hsn'] or 'Not provided'} | Valuation: {bill['method']}")
        df=pd.DataFrame([['Taxable value',bill['taxable']],['CGST',bill['cgst']],['SGST',bill['sgst']],['IGST',bill['igst']],['Cess',bill['cess']],['Total',bill['total']]],columns=['Particulars','Amount (₹)'])
        st.dataframe(df,use_container_width=True,hide_index=True)
        text='GST FINDER - BILL PREVIEW\n'+DISCLAIMER+'\n\n'+ '\n'.join(f'{k}: {v}' for k,v in bill.items())
        st.download_button('Download TXT',text.encode(),file_name='gst_bill_preview.txt',mime='text/plain')
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle
            from reportlab.lib import colors
            from reportlab.lib.styles import getSampleStyleSheet
            buff=io.BytesIO(); doc=SimpleDocTemplate(buff,pagesize=A4); styles=getSampleStyleSheet()
            story=[Paragraph('GST FINDER — BILL PREVIEW',styles['Title']),Paragraph(DISCLAIMER,styles['BodyText']),Spacer(1,16),Paragraph(f"{bill['item']} | HSN/SAC: {bill['hsn'] or 'Not provided'}",styles['BodyText']),Spacer(1,12)]
            data=[['Particulars','Amount (₹)']]+[[k,v] for k,v in [('Taxable value',bill['taxable']),('CGST',bill['cgst']),('SGST',bill['sgst']),('IGST',bill['igst']),('Cess',bill['cess']),('Total',bill['total'])]]
            table=Table(data); table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dce8f7')),('GRID',(0,0),(-1,-1),.5,colors.grey),('PADDING',(0,0),(-1,-1),7)])); story.append(table); doc.build(story)
            st.download_button('Download PDF',buff.getvalue(),file_name='gst_bill_preview.pdf',mime='application/pdf')
        except Exception as ex: st.error(f'PDF generation unavailable: {ex}')
        try:
            from docx import Document
            d=Document(); d.add_heading('GST FINDER — BILL PREVIEW',0); d.add_paragraph(DISCLAIMER); d.add_paragraph(f"{bill['item']} | HSN/SAC: {bill['hsn'] or 'Not provided'}")
            t=d.add_table(rows=1,cols=2); t.rows[0].cells[0].text='Particulars'; t.rows[0].cells[1].text='Amount (₹)'
            for k,v in [('Taxable value',bill['taxable']),('CGST',bill['cgst']),('SGST',bill['sgst']),('IGST',bill['igst']),('Cess',bill['cess']),('Total',bill['total'])]:
                cells=t.add_row().cells; cells[0].text=k; cells[1].text=v
            out=io.BytesIO(); d.save(out); st.download_button('Download DOCX',out.getvalue(),file_name='gst_bill_preview.docx',mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        except Exception as ex: st.error(f'DOCX generation unavailable: {ex}')

def valuation_page():
    st.title('GST VALUATION WORKSHEET')
    st.info('This worksheet is an educational aid. The correct legal method depends on the transaction facts and statutory conditions. Do not auto-select a valuation rule without review.')
    method=st.selectbox('Transaction situation',['Normal transaction value — Section 15','Non-monetary consideration — Rule 27','Related / distinct persons — Rule 28','Principal-agent goods — Rule 29','Cost method — Rule 30','Residual method — Rule 31','Pure agent expenditure — Rule 33'])
    with st.form('valuation'):
        price=st.text_input('Price actually paid/payable (₹)','10000')
        additions=st.text_input('Other includible amounts (₹)','0')
        discount=st.text_input('Eligible discount to exclude (₹)','0')
        cost=st.text_input('Cost of supply, if relevant (₹)','0')
        submitted=st.form_submit_button('CALCULATE INDICATIVE VALUE')
    if submitted:
        try:
            p,a,d,c=map(money,[price,additions,discount,cost])
            if min(p,a,d,c)<0: raise ValueError('Enter non-negative values.')
            if d>p+a: raise ValueError('Discount cannot exceed price plus additions in this worksheet.')
            value=p+a-d
            if method=='Cost method — Rule 30': value=(c*Decimal('1.10')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            st.metric('Indicative taxable value',fmt(value))
            st.write('**Selected workflow:**',method)
            st.warning('Rule 27–31 require statutory sequencing and evidence. Rule 33 pure-agent exclusion is available only when every prescribed condition is satisfied. This simple worksheet does not establish those conditions or replace the full rule analysis.')
            save_history('Valuation worksheet',method,f'Indicative value {value}')
        except Exception as ex: st.error(str(ex))
    with st.expander('Rule reference guide'):
        st.markdown('''- **Section 15:** transaction value subject to statutory conditions, inclusions and exclusions.
- **Rule 27:** consideration not wholly in money.
- **Rule 28:** supplies between distinct or related persons (other than through an agent).
- **Rule 29:** goods supplied through an agent.
- **Rule 30:** 110% of cost, where applicable in the prescribed sequence.
- **Rule 31:** residual method.
- **Rule 33:** expenditure incurred as a pure agent, subject to all conditions.''')

def info_page():
    st.title('INFO & DATABASE')
    st.markdown('''### How to search
Enter any part of a product name, common alias or HSN heading. Search is case-insensitive and returns all matching records. A partial HSN search is supported.

### GST terminology
- **CGST + SGST:** generally applies to intra-state taxable supplies.
- **IGST:** generally applies to inter-state taxable supplies.
- **Exempt / nil-rated / zero-rated / non-GST:** these are distinct legal treatments and must not be treated as synonyms.
- **HSN:** Harmonised System of Nomenclature classification code for goods; SAC is used for services.

### Database limitations
The included starter index is deliberately small. It is not a complete or automatically updated tariff database. The uploaded CBIC schedule in the source research compilation is labelled 01-04-2023 and must not be treated as a current rate master without amendment verification. The 2025 research notes are secondary material; confirm legal changes against notifications.
''')
    st.markdown(f'**Official sources:** [CBIC GST rates]({CBIC_URL}) · [GST Council rate notifications]({COUNCIL_URL})')
    st.subheader('Import reviewed product records (CSV)')
    st.caption('CSV columns: name, description, category, hsn, rate, aliases, status, source, effective, manufacturer. Imported records are marked exactly as supplied; verify them before use.')
    template='name,description,category,hsn,rate,aliases,status,source,effective,manufacturer\nExample item,Description,Dairy,0401,0,alias one;alias two,Needs review,https://cbic-gst.gov.in,Verify date,\n'
    st.download_button('Download CSV template',template,file_name='gst_products_template.csv',mime='text/csv')
    upload=st.file_uploader('Upload reviewed CSV',type=['csv'],help='Uploads are processed locally by this application; do not upload personal taxpayer documents.')
    if upload and st.button('Import CSV into product database'):
        try:
            df=pd.read_csv(upload,dtype=str).fillna('')
            required={'name','description','category','hsn','rate','aliases','status','source','effective'}
            if not required.issubset(df.columns): st.error('Missing required columns: '+', '.join(sorted(required-set(df.columns))))
            else:
                for _,r in df.iterrows():
                    if not r['name'].strip() or not re.fullmatch(r'\d+(?:\.\d+)?',r['rate'].strip()): continue
                    with db() as c: c.execute('INSERT INTO products(name,description,category,hsn,rate,aliases,status,source,effective,manufacturer) VALUES(?,?,?,?,?,?,?,?,?,?)',(r['name'],r['description'],r['category'],r['hsn'],r['rate'],r['aliases'],r['status'],r['source'],r['effective'],r.get('manufacturer','')))
                st.success('Import completed. Refresh the Home page and search again.')
        except Exception as ex: st.error(f'Import failed: {ex}')
    with db() as c: count=c.execute('SELECT COUNT(*) FROM products WHERE active=1').fetchone()[0]
    st.metric('Active product records',count)

def history_page():
    st.title('HISTORY')
    uid=st.session_state.get('user_id')
    if uid is None:
        st.info('Guest searches and calculations are not saved to the database. Sign in to keep history.')
        return
    with db() as c: rows=c.execute('SELECT id,kind,query,result,created_at FROM history WHERE user_id=? ORDER BY id DESC LIMIT 500',(uid,)).fetchall()
    if not rows: st.info('No saved history yet.'); return
    df=pd.DataFrame([dict(r) for r in rows])
    filt=st.text_input('Filter history')
    if filt: df=df[df.astype(str).apply(lambda col: col.str.contains(filt,case=False,na=False)).any(axis=1)]
    st.dataframe(df,use_container_width=True,hide_index=True)
    ids=st.multiselect('Select history IDs to delete',df['id'].tolist())
    if st.button('Delete selected history',disabled=not ids):
        with db() as c: c.executemany('DELETE FROM history WHERE id=? AND user_id=?',[(int(i),uid) for i in ids])
        st.success('Selected history deleted.'); st.rerun()
    if st.button('Delete all my history'):
        with db() as c: c.execute('DELETE FROM history WHERE user_id=?',(uid,))
        st.success('Your history has been deleted.'); st.rerun()

init_db()
if 'authenticated' not in st.session_state: st.session_state.authenticated=False
if not st.session_state.authenticated:
    auth_panel()
else:
    st.sidebar.write(f"Signed in as **{st.session_state.get('username','Guest')}**")
    if st.sidebar.button('Sign out',use_container_width=True):
        for k in ['authenticated','user_id','username','last_bill']: st.session_state.pop(k,None)
        st.rerun()
page=st.sidebar.radio('Navigation',['HOME','GST CALCULATOR & BILL','VALUATION','INFO','HISTORY'])
st.sidebar.divider()
st.sidebar.caption('Blue-and-white GST research and calculation workspace')
if page=='HOME': home_page()
elif page=='GST CALCULATOR & BILL': calculator_page()
elif page=='VALUATION': valuation_page()
elif page=='INFO': info_page()
else: history_page()
st.divider()
st.warning(DISCLAIMER)
st.caption('Database records are not a substitute for the applicable Acts, Rules, notifications, circulars, tariff notes or professional advice.')
