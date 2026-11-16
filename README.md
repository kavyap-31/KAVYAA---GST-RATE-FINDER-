# GST FINDER — full rebuild

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Google Colab

1. Upload `app.py`, `requirements.txt`, and `gst_goods_historical_2023.csv` into the same Colab working directory.
2. Run `!pip install -r requirements.txt`.
3. Run `!streamlit run app.py &` and expose the Streamlit port using your preferred Colab tunnel.

## Deploy

Push `app.py`, `requirements.txt`, `README.md`, `.gitignore`, and `gst_goods_historical_2023.csv` to a GitHub repository. In Streamlit Community Cloud, select the repository and set the main file to `app.py`.

## Search repair

The app now restores missing starter records on every startup, including the milk records, even when an older SQLite database already contains other products. The bundled CBIC schedule CSV is imported idempotently. Search supports case-insensitive exact and partial text, product descriptions, aliases, category, manufacturer and partial/exact HSN matching, and returns all matches.

## Data limitations

The bundled goods schedule CSV is explicitly historical (CBIC page date label 01-04-2023). Do not treat historical rows as current verified rates. Starter entries and imported rows carry status/source/effective-date fields; verify product composition, packaging/labelling conditions and later notifications before use. The app does not claim to contain a complete live GST tariff database or automatically certify legal classification.

The application is an educational tool. Review authentication, secrets, database persistence, backups, access controls and applicable DPDP requirements before production deployment. Streamlit Community Cloud local SQLite storage should not be treated as durable production storage.
