# Configuration file editor

This tool is for advanced users who need to manage `.env` files directly. For initial setup and everyday configuration, use the backend [configuration UI and setup wizard](quickstart.md).

Generate or edit a backend `.env` file in your browser. The interactive editor is available on the [documentation website](https://kldhsh123.github.io/Afterglow/en/config-editor.html). It does not require a running backend or a setup token.

## Using the editor

1. Start with template defaults, or import an existing UTF-8 `.env` file. Import replaces the current draft; unknown variables are retained. Original comments are replaced with comments in the selected language.
2. Fill in your names and account IDs, chat service, embedding service and backend access key. Confirm identities from your exported chat. Use the provider's base URL, including its required path such as `/v1`, without a `/chat/completions` or `/responses` suffix.
3. Switch to all settings, select a group or search by variable name to adjust advanced options. Descriptions and template defaults come from the [environment reference](environment.md). File templates use backend defaults, which differ from the setup wizard's English service suggestions.
4. Download `.env`, move it to `backend/.env` and fully restart the backend. Keep a backup before replacing existing configuration. Process environment variables override values in the file.

All file processing takes place in the tab. Values are not submitted to a server, stored in browser storage or included in the page URL. In-site language switching keeps the draft and changes descriptions and exported comments. Reloading or closing the tab discards the draft. The backend language is a separate field, `XUWEN_LANGUAGE`, defaulting to `en`; changing the editor's interface language does not change it.

Secret fields and the generated-file preview are hidden initially. Downloaded files contain real keys. Basic checks cover types, explicit field bounds and selected dependencies; full validation happens at backend startup. Empty required values can be saved as a draft. Imported `${VARIABLE}` expressions are preserved for the backend to expand.

## Download a template

If JavaScript is unavailable, download one of these templates and copy it to `backend/.env`. Both contain the same variables and values; only comments differ.

- [English comments: .env.example](https://github.com/kldhsh123/Afterglow/blob/main/backend/.env.example)
- [Chinese comments: .env.example.zh-CN](https://github.com/kldhsh123/Afterglow/blob/main/backend/.env.example.zh-CN)

For authenticated connection checks, chat imports and writing configuration directly on the backend, use its [setup wizard](quickstart.md).
