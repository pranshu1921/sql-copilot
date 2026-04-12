# Running the SQL Co-pilot

Complete instructions for local development and Hugging Face Spaces deployment.

---

## Prerequisites

- Python 3.10 or higher
- Git
- Conda (Anaconda or Miniconda)
- A Hugging Face account at huggingface.co
- A Hugging Face API token with read access (huggingface.co/settings/tokens)
- The Olist dataset CSV files in the `data/` folder (or your own CSVs)

---

## Part 1: Local Development

### Step 1: Clone the repository

```bash
git clone https://github.com/pranshu1921/sql-copilot.git
cd sql-copilot
```

### Step 2: Create and activate conda environment

```bash
conda create -n sql-copilot python=3.11 -y
conda activate sql-copilot
```

You should see `(sql-copilot)` at the start of your terminal prompt.

Every time you open a new terminal to work on this project, run `conda activate sql-copilot` before any Python or pip commands.

### Step 3: Install dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Add your HF token

```bash
cp .env.example .env
```

Open `.env` and replace the placeholder:

```
HF_TOKEN=hf_your_actual_token_here
```

Get your token at huggingface.co/settings/tokens. Create one with read access if you do not have one.

### Step 5: Add data

If you have not already, place the Olist CSV files in the `data/` folder.
Download from: kaggle.com/datasets/olistbr/brazilian-ecommerce

Or drop your own CSV files into `data/` — any CSV works.

### Step 6: Run the application

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`. The data loads on first run (about 2 seconds for CSVs). Then ask any question in the chat input.

---

## Part 2: Using your own data

1. Remove or keep the existing CSV files in `data/`
2. Drop your own CSV files into `data/`
3. Optionally create `data/relationships.txt` with join keys:
   ```
   table1.column = table2.column
   table2.column = table3.column
   ```
4. Optionally drop an ERD image (PNG or JPG) into `data/` — relationships are extracted automatically on first run and cached to `relationships.txt`
5. Restart the app — your tables are immediately queryable

No code changes required.

---

## Part 3: Deploying to Hugging Face Spaces

### Step 1: Create a Hugging Face account

Go to huggingface.co and sign up. Your Space URL will be `huggingface.co/spaces/your-username/sql-copilot`.

### Step 2: Create a new Space

1. Go to huggingface.co/new-space
2. Space name: `sql-copilot`
3. SDK: Streamlit
4. Visibility: Public
5. Click Create Space

### Step 3: Add your HF token as a secret

Space Settings > Variables and Secrets > New Secret

Name: `HF_TOKEN`
Value: your Hugging Face API token

### Step 4: Add HF Spaces as a git remote

```bash
git remote add hfspace https://huggingface.co/spaces/YOUR_HF_USERNAME/sql-copilot
```

Replace `YOUR_HF_USERNAME` with your actual HF username.

### Step 5: Push to HF Spaces

```bash
git push hfspace main
```

HF installs dependencies and starts the app automatically. Build takes about 90 seconds on first push. Watch progress in the Logs tab of your Space.

### Step 6: Update the live demo URL in README.md

Open `README.md` and update line 8:

```
Live demo: huggingface.co/spaces/YOUR_HF_USERNAME/sql-copilot
```

Then commit and push:

```bash
git add README.md
git commit -m "chore: update live demo URL"
git push origin main
git push hfspace main
```

---

## Part 4: Keeping GitHub and HF Spaces in sync

```bash
git push origin main       # pushes to GitHub
git push hfspace main      # triggers a rebuild on HF Spaces
```

---

## Common issues

**HF_TOKEN not found**

Check that `.env` exists in the project root and contains `HF_TOKEN=hf_...` with no spaces around the equals sign.

**No CSV files found**

Confirm CSV files are in the `data/` folder directly — not in a subfolder. Run `ls data/` to check.

**Query fails on all 3 attempts**

The question may reference columns or relationships the model cannot infer. Try rephrasing to be more specific about which tables or fields you want. Adding a `relationships.txt` file also improves accuracy significantly.

**HF Inference API rate limit**

The free tier allows approximately 30 requests per hour. Wait a few minutes and retry.

**Space shows an error after push**

Check the Logs tab in your Space. The most common cause is a missing dependency in `requirements.txt` or an import error. Fix, commit, and push again.

**Cold start on HF Spaces**

If the Space was just deployed or restarted, the first request loads all CSV data into DuckDB. This takes 2 to 5 seconds. Subsequent queries are instant.
