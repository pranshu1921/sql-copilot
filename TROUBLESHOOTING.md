# Troubleshooting

Real issues encountered during development and deployment of this project,
with exact error messages and fixes. Ordered by when they typically appear.

---

## Local development issues

### pip install does nothing or installs into wrong environment

**Symptom:**
```
pip install -r requirements.txt
# nothing happens, or packages install into base conda environment
```

**Cause:** Conda base environment is active instead of the project environment.

**Fix:**
```bash
conda activate sql-copilot
pip install -r requirements.txt
```

Always confirm your prompt starts with `(sql-copilot)` before running any
pip or Python commands. If it shows `(base)`, activate first.

---

### stray file `=0.23.4` appears in project root

**Symptom:** `git status` shows an untracked file called `=0.23.4`

**Cause:** A pip install command was run with `==` interpreted as a filename:
```bash
pip install huggingface-hub>=0.23.4   # the >= got parsed incorrectly
```

**Fix:**
```bash
rm "=0.23.4"
```

---

### `.env` or `.venv` showing as untracked in git status

**Symptom:**
```
Untracked files:
    .env
    .venv/
```

**Fix:** Confirm `.gitignore` contains both entries, then remove from git cache:
```bash
git rm --cached .env 2>/dev/null
git rm -r --cached .venv/ 2>/dev/null
git add .gitignore
git commit -m "chore: fix gitignore for .env and .venv"
```

---

## HF Inference API issues

### 410 Gone — model no longer available

**Symptom:**
```
requests.exceptions.HTTPError: 410 Client Error: Gone for url:
https://api-inference.huggingface.co/models/defog/sqlcoder-7b-2
```

**Cause:** `defog/sqlcoder-7b-2` was removed from the HF free Inference API.

**Fix:** Switch to `Qwen/Qwen2.5-Coder-7B-Instruct` in `agent.py`:
```python
SQLCODER_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"
```

Also upgrade huggingface-hub to support the new router endpoint:
```bash
pip install --upgrade huggingface-hub
```

---

### text_generation task not supported for provider

**Symptom:**
```
ValueError: Task 'text-generation' not supported for provider 'nscale'.
Available tasks: ['conversational', 'text-to-image']
```

**Cause:** HF moved from `api-inference.huggingface.co` to `router.huggingface.co`.
The old `text_generation()` method is no longer compatible.

**Fix:** Switch from `text_generation()` to `chat_completion()` in `agent.py`:
```python
# Old — remove this
response = self.client.text_generation(
    prompt,
    model=SQLCODER_MODEL,
    max_new_tokens=512,
)

# New — use this
response = self.client.chat_completion(
    model=SQLCODER_MODEL,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ],
    max_tokens=512,
    temperature=0.01,
)
raw = response.choices[0].message.content or ""
```

---

## SQL generation issues

### Binder Error — wrong table alias used

**Symptom:**
```
Binder Error: Values list "p" does not have a column named "product_category_name_english"
```

**Cause:** The model generated table aliases (`p`, `r`, `o`) and then used columns
from the wrong table under those aliases.

**Fix:** SQLGlot AST rewriting in `agent.py` strips all aliases and rewrites
column references to use full table names. See `_strip_aliases()` method.

---

### Binder Error — column in wrong table after alias stripping

**Symptom:**
```
Binder Error: Values list "olist_products_dataset" does not have a column named
"product_category_name_english"
```

**Cause:** After aliases are stripped, the model still assigned a column to the
wrong table. `product_category_name_english` only exists in
`product_category_name_translation`, not in `olist_products_dataset`.

**Fix 1:** Add `data/relationships.txt` with explicit join paths so the model
knows the correct table relationships.

**Fix 2:** The `_fix_column_tables()` method in `agent.py` post-processes every
`table.column` reference, checks it against the real DuckDB schema, and rewrites
it to the correct table automatically.

Both fixes work together — `relationships.txt` prevents the error at generation
time, `_fix_column_tables()` catches anything that slips through.

---

### Model ignores "no aliases" instruction in system prompt

**Cause:** 7B parameter models do not reliably follow negative instructions
("do not use aliases"). Prompt instructions alone cannot solve this.

**Fix:** Do not rely on prompting for this. Use deterministic post-processing:
`_strip_aliases()` in `agent.py` rewrites the SQL AST regardless of what the
model generates. The fix is in code, not in the prompt.

---

## Streamlit UI issues

### Secrets file not found warning on home page (local)

**Symptom:**
```
FileNotFoundError: No secrets files found. Valid paths for a secrets.toml file are:
C:\Users\username\.streamlit\secrets.toml
```

**Cause:** `st.secrets` is accessed before checking for the `.env` file.
Streamlit raises this error even with a try/except in older versions.

**Fix:** Check `os.getenv("HF_TOKEN")` first. Only fall back to `st.secrets`
if the environment variable is not set:

```python
def get_hf_token() -> str:
    token = os.getenv("HF_TOKEN", "")
    if token:
        return token
    try:
        if "HF_TOKEN" in st.secrets:
            return st.secrets["HF_TOKEN"]
    except (FileNotFoundError, KeyError):
        pass
    st.error("HF_TOKEN not found.")
    st.stop()
    return ""
```

---

## HF Spaces deployment issues

### Push rejected — not authorized

**Symptom:**
```
remote: You are not authorized to push to this repo.
```

**Cause:** HF token used for push has read-only access.

**Fix:** Create a new token at `huggingface.co/settings/tokens` with **Write**
access. Use that token in the GitHub Actions secret and in the remote URL.

---

### Push rejected — files larger than 10 MiB

**Symptom:**
```
remote: Your push was rejected because it contains files larger than 10 MiB.
remote: Offending files:
remote:   - data/olist_geolocation_dataset.csv
remote:   - data/olist_order_items_dataset.csv
```

**Cause:** HF Spaces requires Git LFS for files over 10 MB. Direct git push
sends raw file content instead of LFS pointers.

**Fix:** Use the HF Python API to upload files instead of git push.
In `.github/workflows/deploy.yml`:

```yaml
- name: Upload to HF Spaces
  env:
    HF_TOKEN: ${{ secrets.HF_TOKEN }}
  run: |
    pip install huggingface_hub
    python - <<'EOF'
    import os
    from huggingface_hub import HfApi

    api = HfApi(token=os.environ["HF_TOKEN"])
    api.upload_folder(
        folder_path=".",
        repo_id="pranshu2230/sql-copilot",
        repo_type="space",
        ignore_patterns=[
            ".git*", ".github*", ".env",
            ".venv", "__pycache__", "*.pyc",
            "test_db.py",
        ],
    )
    EOF
```

This bypasses git entirely for the HF push and handles large files natively.

---

### Secrets file not found error on HF Spaces

**Symptom:** App loads but shows:
```
No secrets files found. Valid paths for a secrets.toml file are:
/root/.streamlit/secrets.toml, /app/.streamlit/secrets.toml
```

**Cause 1:** `HF_TOKEN` secret not added to the Space.

**Fix:** Go to `huggingface.co/spaces/pranshu2230/sql-copilot` →
Settings → Variables and secrets → New secret → Name: `HF_TOKEN` →
Value: your token → Save → Restart space.

**Cause 2:** `get_hf_token()` calls `st.secrets` before checking the
environment variable. HF Spaces injects secrets as env vars, not as a
secrets.toml file.

**Fix:** Always check `os.getenv("HF_TOKEN")` first. See the fix above
under "Secrets file not found warning on home page".

---

### HF Spaces has no GitHub sync option in Settings

**Symptom:** Settings page does not show a "Repository" or "GitHub sync" section.

**Cause:** HF removed the GitHub sync UI feature in a recent update.

**Fix:** Use GitHub Actions to push to HF Spaces on every commit to main.
See `.github/workflows/deploy.yml` in this repo for the full workflow.
The workflow uses `huggingface_hub.HfApi.upload_folder()` to upload files
directly, bypassing git push entirely.

---

### RUNNING status but app not loading after first deploy

**Cause:** HF Spaces shows "Running" as soon as the container starts, but the
app may still be installing dependencies or loading data.

**Fix:** Click the **Logs** tab in your Space to see real-time output.
Wait for the line:
```
You can now view your Streamlit app in your browser
```
before expecting the app to respond.
