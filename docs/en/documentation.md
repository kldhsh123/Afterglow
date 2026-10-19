# Documentation and Wiki maintenance

## Authoritative sources

The primary reader interface is the bilingual VitePress website on GitHub Pages. Each article is maintained once per language:

- Chinese: `docs/wiki/`, retaining the existing filenames and repository links.
- English: `docs/en/`, one independent Markdown file per article.
- Shared page mapping: `docs/pages.json`; `slug` is identical under `/en/` and `/zh/`.
- Site UI: `docs/.vitepress/locales/en.mjs` and `zh-CN.mjs`, one language per file.

Generated `.site/`, `.vitepress/dist/` and Wiki checkouts are not authoritative sources. Update both language articles in the same PR. Do not publish English summaries in place of complete translations.

`README.md` (English) and `readme/README.zh-CN.md` (Chinese) are equal project entrances. Their architecture diagrams are intentional summaries; update them alongside both full architecture pages when the design changes. Backend/frontend READMEs remain package-specific entries.

## Page organization

To add a page:

1. Add a Chinese article in `docs/wiki/` and its English counterpart in `docs/en/`.
2. Add the shared slug, Chinese filename and navigation group to `docs/pages.json`; titles for site navigation are in each locale module's `pages` map.
3. Keep corresponding headings in the same order and at the same level, so switching languages retains the section.
4. Link the page from related articles and the documentation home.
5. Run checks, tests and the production build before submitting.

The generator makes `/en/<slug>.html` and `/zh/<slug>.html`. Locale homes are `/en/` and `/zh/`. Internal source links stay relative `.md` links so GitHub source browsing works. Generated section IDs pair matching headings; readable heading aliases preserve direct section links.

Default language is English. On first visit to the site root, any Simplified/Traditional Chinese browser preference selects Chinese; otherwise English is selected. The dropdown remembers the manual choice in `afterglow.docs.locale`. Explicit article URLs remain in their requested language, so shared links are predictable.

Local development (Python 3 and Node.js 20+):

```bash
cd docs
npm ci
npm run dev
```

Sources are generated before starting VitePress; rerun `npm run prepare:docs` after changing source articles during development. Check and preview:

```bash
npm run check
npm test
npm run build
npm run preview
```

Default site base is `/Afterglow/`. For a custom domain or local root deployment, build with `DOCS_BASE=/ npm run build`. For a different project name, set `DOCS_BASE=/your-project/`. Keep the Wiki's `DOCS_SITE_URL` repository variable aligned with the actual site URL.

## Enable GitHub Pages

A maintainer performs the initial setup:

1. Open repository **Settings → Pages** and choose **GitHub Actions** as the source.
2. Merge the documentation workflow and sources into `main`, or run `docs-pages` manually on the desired branch.
3. Read the deployed URL from the `github-pages` environment/deployment output.

The default address is `https://kldhsh123.github.io/Afterglow/`. Forks derive their base path from the repository name; a custom domain requires the appropriate Pages/domain configuration and matching base setting. The workflow has a `base_path` manual input and supports the repository variable `DOCS_BASE`.

Wiki support is optional. To initialize it, enable Wikis in repository Settings, restrict editing to collaborators and create an initial `Home` page so the Wiki Git repository can be cloned. `sync-wiki` uses `GITHUB_TOKEN`, or `WIKI_TOKEN` if repository policy requires a separate write credential.

## Publishing behavior

`.github/workflows/docs-pages.yml` validates and builds documentation PRs without publishing. Pushes to `main`, published releases and manual runs build then deploy the static artifact to Pages. Release runs use the release tag's sources. PRs receive no deployment permission. Deployments share a concurrency group to avoid racing releases.

`.github/workflows/sync-wiki.yml` validates documentation PRs; only published releases and manual runs write the Wiki. It generates a flat mirror: old Chinese page names remain unchanged, English pages use `EN-<slug>`, and every page links to the website and corresponding language. Sidebar entries expose both languages equally. Existing Chinese Wiki section links are kept by retaining source headings.

The Wiki checkout is replaced with the generated mirror via `rsync --delete`, excluding `.git/`. Only changed content is committed and pushed, without force-push. Repository sources are never rewritten by publication.

## Generation scripts

`.github/scripts/prepare_docs.py` reads both language sources and the shared mapping. It checks corresponding section counts, source existence and mapped relative links, rejects source-directory output, and produces either a site tree or a flat Wiki preview. `.github/scripts/prepare_wiki.py` validates/replaces internal links in the generated Wiki only.

From the repository root, preview Wiki generation safely:

```bash
python3 .github/scripts/prepare_docs.py wiki --output /tmp/afterglow-wiki-preview
```

Never point the Wiki link-rewriter at source directories; it modifies its input in place. The site build also validates rendered links and translation structure through `docs/scripts/`.

References:

- [GitHub Pages](https://docs.github.com/en/pages)
- [GitHub Wikis](https://docs.github.com/en/communities/documenting-your-project-with-wikis)
- [GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token)
- [Actions concurrency](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency)

The configuration editor schema is generated from backend Settings, both `.env.example` templates and both environment references. Keep all fields and template values synchronized; the build rejects missing fields or mismatched values. Its UI strings live in the site locale modules; file parsing/serialization is covered by the Node tests.

Brand images remain authoritative in `assets/brand/`. The site generator copies the selected logo and favicons into its generated public directory. Both the README and navbar use the existing light/dark variants; update the source assets to change the identity.
