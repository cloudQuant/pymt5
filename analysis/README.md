# Static protocol analysis helpers

The four Python scripts in this directory statically inspect MT5 Web Terminal
JavaScript snapshots. They are development research tools, not runtime modules,
and are excluded from the pymt5 distributions.

Third-party JavaScript snapshots are intentionally not tracked in this
repository. Keep any lawfully obtained snapshots in a private directory outside
the checkout and point the scripts at that directory:

```bash
export PYMT5_ANALYSIS_DIR=/path/to/private/mt5-js
python analysis/extract_protocol.py
python analysis/extract_commands.py
python analysis/extract_crypto.py
python analysis/deep_protocol.py
```

The scripts print excerpts from their inputs. Do not commit generated output or
copy private snapshots back into this directory. The repository MIT license does
not grant rights to third-party bundles; retaining them privately does not
replace any applicable license obligations, and removing them from the current
worktree does not rewrite Git history.
