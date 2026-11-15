# pymt5 documentation

The public site has two complete, parallel source trees:

- `docs/*.rst` — English, published by GitHub Pages under `/en/`
- `docs/zh_CN/*.rst` — Simplified Chinese, published by GitHub Pages under `/zh-CN/`

Every public English page must have a Chinese counterpart with the same file
name. Keep command IDs, Python identifiers, code examples, protocol limitations,
and safety warnings semantically synchronized. Do not translate Python names or
weaken a limitation in one language.

## Build both sites

From the repository root:

```bash
python -m pip install -e ".[docs]"
python docs/build_docs.py
python docs/check_docs.py
```

The combined artifact is written to `docs/_build/html/`:

```text
index.html          language selection
en/                 English site
zh-CN/              中文站点
.nojekyll           GitHub Pages static-file marker
```

`docs/build_docs.py` uses the same `docs/conf.py`, templates, and static assets
for both sites. Language switches are static HTML links that preserve the
current page name; JavaScript is not required. The checker validates generated
local links, fragments, HTML language metadata, and switch links.

To preview locally:

```bash
python -m http.server 8000 --directory docs/_build/html
```

Open `http://localhost:8000/`. Do not publish `docs/_build`; it is generated and
Git-ignored.

## Authoring conventions

- Lead with the reader's task, then provide a minimal safe example.
- Default examples to read-only operations and environment-based credentials.
- Mark trading, account creation, notification, and other mutating examples.
- Distinguish offline tests, live observations, and unverified compatibility.
- Prefer task guides for explanation and `api.rst` for generated API details.
- Keep internal iteration plans out of the public toctree.
- Run both Sphinx builds with warnings treated as errors before submitting.

GitHub Actions runs the same build/check commands. The Pages workflow uploads
the combined artifact; deployment happens only through GitHub Pages after an
eligible branch push or manual workflow run.

## Author profile data

`author.rst` and `zh_CN/author.rst` keep a static, accessible introduction and
GitHub links. `docs/_static/author.js` progressively adds public profile counts
and recently updated repositories from GitHub's public REST API. It requires no
token, credentials, third-party script, or build-time network access. Public
responses are cached in session storage for ten minutes per browser tab; API
errors and rate limits leave the static content usable. Render remote strings
only through DOM text APIs and allow dynamic links only to HTTPS GitHub URLs.

## Read the Docs

Read the Docs builds one language per project. The English `pymt5` project is the
parent; a Simplified Chinese project must be created and linked as a translation
in the Read the Docs dashboard. Both use `.readthedocs.yaml`, which selects the
source tree from `READTHEDOCS_LANGUAGE` and runs:

```bash
python docs/build_docs.py --readthedocs
```

The command writes directly to `$READTHEDOCS_OUTPUT/html`; it does not create the
GitHub Pages language selector or nested `/en/` and `/zh-CN/` directories.
Canonical URLs come from `READTHEDOCS_CANONICAL_URL`. The normal GitHub Pages
build and checker remain the required pre-merge validation for both source trees.
