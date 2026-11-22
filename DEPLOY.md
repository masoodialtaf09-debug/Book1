# Deploy Relay on free tiers (Render + Supabase + Stripe), on your own domain

Local trial first (single user, in memory):

    pip install -r requirements-server.txt
    export ANTHROPIC_API_KEY=...
    uvicorn server:app --reload          # http://localhost:8000

## 1. Database and sign-in (Supabase, free plan)
1. Create a project at supabase.com. In Authentication, enable email sign-in.
2. SQL editor: paste and run `schema.sql`. New accounts get 1 free credit (change `default 1` in the file first if you want 0).
3. Settings > API: copy the project URL, anon key and service_role key. The service key is secret: it goes only into Render.
4. Free projects pause after a week with no activity. Opening the dashboard or real traffic counts as activity.

## 2. Code on GitHub
Create a private repo and push this folder's contents to it (`.env` is already ignored).

## 3. Hosting (Render, free web service)
1. render.com > New > Blueprint > pick the repo. It reads `render.yaml`.
2. Fill in the prompted values. Leave Stripe blank for now to test without billing. Set `BASE_URL` to `https://relay.diamondwhitedeal.online`.
3. Free services sleep after 15 minutes idle and take about a minute to wake. Upgrade the service when revenue allows.

## 4. Your domain (GoDaddy)
1. In Render: service > Settings > Custom Domains > add `relay.diamondwhitedeal.online`. Render shows the target address.
2. GoDaddy: My Products > your domain > DNS > Add record. Type CNAME, Name `relay`, Value = the address Render shows. Save.
3. Wait for DNS (minutes to hours). Render then issues the HTTPS certificate on its own.

## 5. Payments (Stripe)
1. Create a product with a one-time price for one credit pack. Copy its price ID (`price_...`) into `STRIPE_PRICE_ID`; set `STRIPE_SECRET_KEY`.
2. Developers > Webhooks > add `https://relay.diamondwhitedeal.online/api/stripe-webhook`, event `checkout.session.completed`. Copy the signing secret into `STRIPE_WEBHOOK_SECRET`.
3. Test with Stripe test mode keys before switching to live keys.

## Before you charge anyone
- Price a credit above its real model cost. One run is about 6 to 10 API calls; do one real run and check each provider's usage page.
- Keep one server instance. Active-run tracking is in memory.
- Say in your privacy policy that project text goes to Anthropic, OpenAI, Perplexity, DeepSeek, Google and GitHub Models. DeepSeek is hosted in China; consider making it optional.
- Free Supabase has no backups. Export your data regularly, or move to Pro once people depend on it.
- Set spending limits in each model provider's dashboard.
