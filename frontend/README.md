# AI Annotator frontend

Next.js (App Router) + TypeScript + Tailwind CSS + PDF.js. See the repository README for setup.

```bash
npm run dev        # development server (dev.py at the repo root starts everything)
npm run lint
npm run typecheck
npm run build
npm run e2e -- ../backend/some.pdf   # browser smoke test against a running stack in test mode
```

The browser talks to the backend at `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).
