# web — eUI front end

Angular 21 + eUI 21.4 (generated with `@eui/cli`, default eUI app shell), one page: chat with streamed answers,
memory and sources side panel, technical details.

* `yarn start` — dev server on :4200, proxies `/api` and `/v1` to the agent on :8000 (`proxy.conf.json`)
* `yarn build` — production build into `dist/browser`
* `Dockerfile` — builds the app and serves it with nginx; `nginx/default.conf.template` forwards `/api/*` and
  `POST /v1/chat/completions` to `AGENT_URL` (Railway private network) and nothing else.

Main code: `src/app/features/assistant/` (page), `src/app/core/assistant-api.service.ts` (streaming client),
`src/app/core/markdown.ts` (escaped, minimal Markdown rendering).
