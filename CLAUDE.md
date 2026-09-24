# Ahavah API

The backend for Ahavah (ahavah.app), a fork of Duolicious. The GitHub repository keeps the
upstream name `techbase-solutions-llc/duolicious-backend`. All Ahavah work is on the branch
`ahavah/main`; upstream's `main` is left at the fork point. A push to `ahavah/main` deploys to
the production droplet through GitHub Actions. See `README.md` and `DEVELOPER.md` for the stack.

## Linear

Linear is Techbase's source of truth. This repo's project is **Ahavah API**:
https://linear.app/techbaseltd/project/ahavah-api-7cac6a28352d

Sister projects (label `Client: Ahavah`): Ahavah Web App (`ahavah-web`), Ahavah Admin,
Ahavah Frontend (legacy Duolicious app) and Ahavah Design Tokens.

Workspace `linear.app/techbaseltd`, one team `Techbase Solutions LLC`, key `TEC`, no cycles.
The working agreement is the team document "How Techbase uses Linear":
https://linear.app/techbaseltd/document/how-techbase-uses-linear-6514569b9511

- **Every approved plan is an issue, created before execution.** Put `Linear: TEC-NN` on the
  plan file's first line. Plans live in `docs/superpowers/plans/`; the committed ones already
  carry their issue. Cross-repo work (Spotlight, verification) is planned here.
- **Every session record names its issue.** This repo keeps no regular session records; the
  handovers in `docs/superpowers/handovers/` carry their issue under the H1. The rest of the
  history comes from git (Ahavah-era commits only, from 12 May 2026) and the plans.
- **"What remains" becomes issues**, in the right milestone. Anything the client owes gets the
  `Client action` label and stays in Todo; a call owed gets `Decision`.
- **Commits reference the issue**, `Refs: TEC-NN` in the footer.

Run `/linear-sync` at the end of every session, before the final commit. It reconciles what is
on disk against the Done issues in Linear and reports the difference before changing anything.
Never assume the last sync was complete.
