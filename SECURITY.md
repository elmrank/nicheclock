# Security policy

## Reporting a vulnerability

Use GitHub's private reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Please do not open a public issue or pull request for a security problem.

Include what you found, the steps to reproduce it, the version or commit, and what an attacker gains. Remove real keys and tokens from anything you send.

This is a small project run by one maintainer. Expect a first reply within a week, and a fix or a written assessment after that. There is no bug bounty.

## Supported versions

Only the latest commit on `main` is supported. There are no release branches yet.

## Scope

In scope:

- the `nicheclock` Python package and its Docker image;
- the Cloudflare Worker in `hosted/` (REST API, remote MCP, key handling, the static site);
- anything that leaks a secret, runs code for an untrusted input, or sends data somewhere the docs say it does not go.

Out of scope: findings that need access to your own machine, config file or `.env`; missing rate limits on the static report server (`nicheclock serve` is a plain file server, see [docs/limitations.md](docs/limitations.md)); and vulnerabilities in the data sources or in a model provider.

## What the project does with secrets

Secrets never live in the repository or in `nicheclock.yaml`. Config fields name an environment variable or a file. An inline `api_key:` in the config is rejected. The Search Console token is stored under your own config directory with mode 600.
