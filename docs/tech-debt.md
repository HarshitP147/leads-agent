# Tech Debt Tracker

Shortcuts taken under the 48-hour deadline. Mention the important ones in the README
"Limitations & next steps" section.

| Item | Why deferred | Fix idea |
|---|---|---|
| Token counts approximate (chars/4) unless tiktoken installed | speed | provider tokenizer |
| No persistent cache of fetched pages | scope | on-disk cache keyed by URL+date |
| No CAPTCHA/stealth handling | ethics + scope | official APIs or data providers |
| Single LLM call per domain; large sites truncated | cost | map-reduce over pages |
