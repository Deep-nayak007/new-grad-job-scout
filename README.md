# Job Scout — New Grad 2027

Job Scout is a private local job-search dashboard for US full-time software development, AI/ML, and data roles. It refreshes curated public feeds, removes duplicate listings, preserves your application statuses in SQLite, exports a multi-tab Excel workbook, and can notify you when a refresh discovers new jobs.

It also includes a cloud build designed for GitHub Pages. GitHub Actions rebuilds the hosted dashboard every day at **8:15 AM Arizona time**, so the permanent URL remains current even when the Mac is off.

## Open the public website

**[https://deep-nayak007.github.io/new-grad-job-scout/](https://deep-nayak007.github.io/new-grad-job-scout/)**

This is the permanent public URL. It works from any computer or phone and does not require the local Python app, your Mac, or a GitHub login.

## Optional local version

Double-click **Job Scout.app** or **run_job_scout.command** in this folder. Your browser opens at [http://127.0.0.1:8765](http://127.0.0.1:8765).

On first launch, the app downloads and indexes the current jobs. This first load establishes a baseline and intentionally does not send hundreds of “new job” alerts. Later refreshes notify only for application URLs that have not appeared before.

No package installation, login, API key, or résumé upload is required. The app uses Python's standard library and keeps its database on this computer.

## Daily updates and notifications

- While the app is open, it checks once per minute and refreshes after 8:00 AM if it has not refreshed that day.
- Opening it after more than six hours also starts a refresh.
- Click **Enable alerts** once in the dashboard for browser notifications. macOS notifications are also sent when a refresh finds new jobs.
- To refresh the workbook every day even when the app is closed, run `scripts/install_daily_refresh.sh` once. It installs a per-user macOS LaunchAgent for 8:00 AM.

The current workbook is always available at `exports/Job_Scout_New_Grad_2027.xlsx` and from the **Excel** button. It contains:

1. **All Matches** — every deduplicated role in scope.
2. **H1B Sponsorship** — roles with explicit sponsorship or a recent employer sponsorship-history signal.
3. **My Applications** — jobs you saved or moved beyond “Not applied.”

## Sources and sponsorship meaning

The main feed cross-checks direct ATS pages with Jobright, Simplify, SpeedyApply, ApplyGuy, Keryx, V's new-grad list, and filtered Zapply feeds. It then discovers the underlying employer boards and queries public **Greenhouse, Lever, and Ashby** posting APIs directly. LinkedIn and Indeed are provided as one-click searches instead of credential-based scraping.

Visa labels are leads, not legal conclusions:

- **Explicit** means Jobright reports that the job description mentions H-1B sponsorship.
- **Likely** means the employer/category has recent sponsorship history.
- **Unknown** means the available feed does not establish sponsorship.
- **Restricted** means the source marks the job as not sponsoring or requiring US citizenship/work authorization.

Always verify the specific job description and ask the recruiter. Employer history never guarantees sponsorship for a particular role.

## Useful commands

```bash
python3 -m job_scout.app                 # run the dashboard
python3 -m job_scout.app --refresh-only  # update data and Excel, then exit
python3 -m job_scout.build_static         # build the GitHub Pages dashboard in docs/
python3 -m unittest discover -s tests    # run tests
```

Data lives in `data/job_scout.db`. Your saved/application status and notes survive refreshes because source updates never overwrite those fields.

## Cloud deployment

The repository includes `.github/workflows/deploy-pages.yml`. On every push to `main`, on a daily schedule, or from the **Run workflow** button, it:

1. Runs the parser/export tests.
2. Fetches every curated and direct-ATS source.
3. Builds `docs/data/jobs.json` and the current Excel workbook.
4. Publishes `docs/` to GitHub Pages.
5. On scheduled or manually triggered runs, records a tiny successful-refresh heartbeat so GitHub does not treat the public repository as inactive and disable its schedule.

On the hosted dashboard, saved jobs and application statuses are stored in that browser's local storage. They remain available on later visits from the same browser without putting personal job-search state in the public repository.
