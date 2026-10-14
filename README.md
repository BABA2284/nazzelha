# Nazzelha | نزّلها

**A mobile-first Arabic RTL media link utility** built to help users inspect supported public video URLs and download media they own or are authorized to save.

> **Development status:** Early-stage project. The repository and deployment configuration are being prepared. End-to-end downloads from YouTube, Instagram, Facebook, and TikTok have not yet been verified in production.

## Goals

- Responsive Arabic-first interface designed for iPhone and mobile browsers.
- Paste a supported public URL and inspect available media formats when the provider permits it.
- Backend-mediated processing for supported URLs.
- Clear download progress and helpful error states.
- Temporary file handling and server-side configuration for sensitive values.
- A deployable container-based backend.

## Responsible use

Use this tool only for content you own or have permission to download, and in ways allowed by each platform's terms and applicable law. Nazzelha does not aim to bypass DRM, paywalls, private-account controls, or other access restrictions. Provider support can change at any time.

## Planned architecture

- **Frontend:** Arabic RTL, responsive web UI suitable for adding to an iPhone Home Screen.
- **Backend:** Python API to validate requests and coordinate supported media processing.
- **Media processing:** FFmpeg where required and legally appropriate.
- **Deployment:** Docker-compatible hosting with HTTPS.

The final technology list and commands should be updated to match the implementation after source files are committed and tested.

## Security principles

- Never commit API keys, passwords, access tokens, or production secrets.
- Configure secrets using your hosting provider's environment-variable settings.
- Use a strong, unique application password if password protection is enabled.
- Restrict downloads to supported providers and validate remote URLs to reduce SSRF risk.
- Apply file-size and runtime limits, clean up temporary files, and avoid storing user URLs longer than necessary.
- Keep dependencies updated and test error handling before exposing a public deployment.

## Development and deployment

The source code and deploy instructions are being added in stages. Do not consider the service production-ready until the dependency installation, automated tests, real supported-URL download tests, HTTPS deployment, and resource limits have been verified.

## Roadmap

- [ ] Commit application source and dependency lock/configuration files.
- [ ] Add automated tests for URL validation, authentication, and download lifecycle.
- [ ] Add deployment configuration and environment-variable documentation.
- [ ] Verify the build and API health check.
- [ ] Test permitted public sample URLs from supported providers.
- [ ] Add screenshots and a live demo after successful deployment.

## Portfolio

This project is being developed as a portfolio project. Feature claims, supported providers, and the live demo link will be updated only after they are verified.

## License

No license has been selected yet. Until a license is added, standard copyright applies and reuse permissions are not granted by default.
