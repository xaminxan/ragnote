# AI Agent Safety Rules

## Git Safety (CRITICAL)

1. **NEVER delete `.git` directory** — this destroys all version history
2. **NEVER use `git reset --hard`** — this destroys uncommitted work
3. **NEVER use `git filter-repo`** — this rewrites history permanently
4. **NEVER use `git push --force`** — this overwrites remote history
5. **Before any file reorganization/refactor**, always:
   - `git add -A && git commit -m "pre-refactor checkpoint"`
   - Verify `git status` shows clean state
6. **After completing changes**, always:
   - `git status` to verify what changed
   - `git diff` to review before committing
   - Commit with descriptive message
7. **If something goes wrong**, use `git restore <file>` to recover — do NOT delete and recreate
8. **NEVER commit sensitive files** — check `.gitignore` before staging. Sensitive files include:
   - `config.json` (contains API keys)
   - `cookies.txt`
   - `.env`
   - Any file with secrets/credentials
