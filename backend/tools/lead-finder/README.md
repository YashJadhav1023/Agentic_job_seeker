# Lead Finder

A free, local web app that finds **founders, decision makers and HR / recruiters** at agencies and tech startups, filtered by **country, state, city and company size**, then helps you write and send **personalised emails** (for example, job applications with your CV) from your own mailbox.

No paid APIs. Everything runs on your machine.

## Features

- **Company discovery**: searches the open web and public LinkedIn company snippets for your industry and location (DuckDuckGo, no API key needed).
- **Location filters**: searchable dropdowns covering 250 countries, their states or regions, and their cities.
- **Company size filter**: 1–10, 11–50, 51–200 … 5k+, using headcount shown in public search snippets.
- **People finder**:
  - Crawls each company's About, Team, Contact and Careers pages.
  - Reads public profile results from LinkedIn, Crunchbase and ZoomInfo.
  - Covers founders, owners, CEOs, CTOs, CMOs and **HR / recruiters / talent acquisition**.
- **Emails, each with a confidence label**:
  - `high`: the email is published on the company's website, or the mail server confirmed it.
  - `medium`: it matches the company's email format, learned from other staff emails on the site.
  - `low`: a guess from the person's name, with alternative formats listed.
- **Inboxes**: HR inboxes (careers@, jobs@, hr@) and general inboxes (info@, hello@) are listed separately.
- **Outreach**:
  - Tick leads, write one template with `{first_name}`, `{company}`, `{role}` and `{my_name}`, and attach your CV.
  - Preview every email before it goes out.
  - **Save as drafts** (Gmail, Outlook, Zoho or any IMAP account) or **send** over SMTP, with spacing between sends and a daily cap.
  - Never emails the same address twice. An Outreach tab tracks what was sent, drafted or failed.
- **Export** to CSV or Excel. Every run is saved locally in SQLite.
- Dark, minimal UI.

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/wasaybuilds/lead-finder.git
cd lead-finder
python -m pip install -r requirements.txt
python app.py
```

It opens at http://127.0.0.1:5050. On Windows you can also double-click `start.bat`.

## Connecting your email (for outreach)

1. Click the ⚙ icon in the top right.
2. **Gmail / Google Workspace**: turn on 2-Step Verification, then create an App Password at <https://myaccount.google.com/apppasswords>. Your normal password won't work.
3. Paste it in, add your name and signature, and click **Test connection**.

Credentials are saved to `settings.json` on your machine only. It is git-ignored, along with `leads.db` and `uploads/`.

## How it works

```
search (DuckDuckGo) ──► company domains + headcount
        │
        ▼
crawl site (about / team / careers / contact) ──► names, roles, published emails
        │
        ▼
public profile snippets (LinkedIn, Crunchbase, ZoomInfo…) ──► more names + roles
        │
        ▼
email: published ▸ learned company pattern ▸ SMTP check ▸ pattern guess
```

## Limits

- **Verification**: most home ISPs block outbound port 25, so guessed emails usually can't be checked with the mail server and stay `low`. Run large lists through an email verifier before sending.
- **Speed**: DuckDuckGo rate-limits searches, so expect roughly 20–40 seconds per company.
- **Company size**: only known when a public snippet shows it. Enable *Only keep companies with a known size* to drop the unknowns.
- **Accuracy**: the people finder is heuristic, so check names using the `profile ↗` link before you send.

## Use responsibly

This tool only reads publicly available pages and search results. You are responsible for how you use it:

- Follow anti-spam and privacy laws where you and your recipients are (CAN-SPAM, GDPR / UK GDPR, CASL, …).
- Keep emails relevant and personal, identify yourself, and honour opt-out requests.
- Respect websites' terms of service and don't hammer them.

## Project structure

```
app.py                  Flask server + API (search jobs, export, settings, outreach)
finder.py               discovery pipeline: companies → people → emails
mailer.py               SMTP send / IMAP drafts, template rendering
static/index.html       single-page UI
static/loc/             per-country states & cities (generated)
tools/build_locations.py   rebuilds static/loc from the source dataset
```

## Credits

Country, state and city data comes from the [countries-states-cities-database](https://github.com/dr5hn/countries-states-cities-database) by dr5hn, licensed under [ODbL-1.0](https://opendatacommons.org/licenses/odbl/1-0/).

## License

[MIT](LICENSE) © 2026 Abdul Wasay
