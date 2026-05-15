# Deployment Notes

This package is a static frontend and can be served behind Nginx, Cloudflare Pages, Vercel static hosting, or any standard web server.

## Minimal static hosting
Upload these files as the web root:
- index.html
- css/
- js/
- assets/

## WebChat backend connection
Edit `js/app.js`:

```js
const SiteConfig = Object.freeze({
  API_BASE_URL: '',
  tenant_key: 'speedaf_public_site',
  channel_key: 'speedaf_webchat',
  session_id: makeId('session'),
  visitor: { source: 'speedaf_public_website' },
  requestTimeoutMs: 3500
});
```

Set `API_BASE_URL` to the production backend base URL when the NexusDesk/WebChat API is ready.
