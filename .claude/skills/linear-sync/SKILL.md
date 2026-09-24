---
name: linear-sync
description: >
  Put a repository, or a whole folder of repositories, into Linear and keep it
  true afterwards. Use this whenever someone wants their projects recorded,
  reviewed or reconciled in Linear: first-time setup ("put my projects on
  Linear", "set this folder up", "onboard my work"), end-of-session updates
  ("sync Linear", "update Linear", "record this session"), and questions about
  state ("has Linear drifted", "what is still open here", "is Linear up to
  date"). Use it at the end of every working session before the final commit,
  and before starting work to find the issue a plan belongs to. Reach for it
  even when Linear is not named but someone is asking for a project's status
  to be written down or brought current. Always fetch from the remote and
  report the difference before changing anything.
---

# linear-sync

Linear is Techbase's source of truth. A source of truth that has drifted is worse than none, so
this skill reads reality first and writes second, every time.

**There is one command.** The person runs `/linear-sync` and you work out what is needed. Never
ask them whether this is setup or a sync, never send them to a different prompt, and never make
them run something else first.

Workspace `linear.app/techbaseltd`, one team `Techbase Solutions LLC`, key `TEC`, no cycles.
The working agreement is the team document **"How Techbase uses Linear"**:
https://linear.app/techbaseltd/document/how-techbase-uses-linear-6514569b9511

Read that document before writing anything to Linear. It holds the conventions and it is kept
current, so do not work from memory of it and do not restate it here.

## How a run goes

1. Work out where you are and what the projects are.
2. Fetch every remote, so you are reading the present rather than a snapshot.
3. Match each project to Linear, if it is already there.
4. Report the difference, and change nothing yet.
5. Write the difference back.
6. Wire the repository so the next person inherits this.
7. Print the numbers.

On a first run over many projects, step 5 pauses after the first one. See **A first run over
many projects** below, and read it before you start rather than when you get there.

## 1. Orient

Look at the current directory before doing anything else.

It is **a single repository** if it has a `.git` directory, a package manifest, or source files
at the top level.

It is **a workspace folder** if it mostly contains subfolders that are themselves projects. This
is the normal case for first-time setup: someone points the skill at the folder holding all their
work.

If it is **neither** — empty, or holding nothing that looks like a project — say so plainly,
explain that `/linear-sync` runs from a folder of projects or from inside one, and stop. Creating
something speculative here is worse than doing nothing.

### If you are inside a single repository, check whether that was intended

Being inside one repository is perfectly normal for the end-of-session sync, so do not interrupt
that. But it is the wrong place to stand for first-time setup, and someone new will often open
Claude Code in the first project they happen to think of rather than in the folder holding all of
them.

So when this repository has **no Linear project yet**, look at the parent folder before you start.
If the parent holds other project folders too, stop and say something like:

> You're inside `cearpro`, which isn't in Linear yet. Its parent folder, `techbase-solutions-llc`,
> holds 20 other projects that also aren't. I can set up just this one, or you can reopen Claude
> Code in the parent folder and I'll do all of them in one pass. Which would you prefer?

Offer both, and do whichever they pick — setting up one project is a perfectly good answer, and
some people genuinely want that. What you are preventing is the person who meant to onboard
everything discovering twenty runs later that there was a single command for it.

If the repository already has a Linear project, or the parent holds nothing else, say nothing and
carry on. A prompt that fires on every routine sync is noise, and noise gets ignored.

In a workspace folder, look one or two levels down, because projects are often grouped by client
(`ncg/mental-essentials`, `unicef/unicef-course-video`). A folder that contains only other
project folders, with no git, no manifest and no source of its own, is a grouping folder and not
a project.

Treat **every project folder as its own Linear project, with no exceptions**. Do not merge thin
ones into a combined project, however tidy that looks. It has been tried and it was rejected,
because a folder folded into someone else's project stops being findable. A one-commit scaffold
gets its own project.

Take the client name from the repository's own documentation. If you cannot tell who the client
is, ask rather than invent one, because the `Client:` label is how the whole workspace is
navigated. If the client is new, create the label.

## 2. Fetch, before reading anything

This is the rule that matters most. For every repository:

```
git -c safe.directory='*' fetch origin
```

Compare `HEAD` against the remote branch. If the checkout is behind, bring it up to date before
reading a single file, and say that you did.

A description written from a stale checkout is not a small error. On 21 September 2026 this step
was skipped across a third of one workspace, and the result was a live event site taking payments
recorded as dormant, an active client platform recorded as abandoned, six live websites recorded
as never deployed, and the production front door of the agency's own domain marked completed.
All of it had to be found and undone by hand. People act on these pages, which is why a wrong one
costs more than a missing one.

Three more things a remote will mislead you about:

- **A redirecting remote URL is not proof of ownership.** GitHub keeps serving the old address
  after a repository is transferred, so `origin` can still name the previous owner long after the
  repository moved. Ask the API for the repository's real full name, and use that name in
  anything you write. Do not read ownership off `git remote -v`.
- **A 404 is not always a permissions problem.** Check whether the repository still exists. Some
  do not, and a project whose only copy is one laptop is worth raising loudly.
- **Some repositories will not fetch normally.** "fetch-pack: invalid index-pack output" usually
  means a large pack rather than a network fault. Try `-c http.version=HTTP/1.1`, then
  `--filter=blob:none`, before concluding anything.

A repository with no remote at all still gets its project. Record the missing remote as a
finding, because it means the work exists in one place only.

## 3. Match before deciding

`list_projects` for the team, then match each folder to an existing project, in this order:

1. The project URL in the repository's `## Linear` section of `CLAUDE.md` or `AGENTS.md`.
2. A project whose linked GitHub repository is this repository.
3. An exact or near-exact name match.

**If a project already exists, update it. Never create a second one.** It does not matter who
made it, how thin it is, or whether it predates this skill. Two pages describing one project
disagree sooner or later, and then neither is trusted.

Create a project only when the match finds nothing. If two candidates look plausible, show both
and ask; a wrong merge is much harder to unpick than a question.

## 4. Reconcile, and report before writing

Gather both sides and print the difference. Create, close and edit nothing yet.

### Find how this repository records its work

**Do not assume a layout.** `docs/sessions/` and `docs/plans/` are one person's convention, used
across part of this workspace. Plenty of repositories do something else, and a repository that
does something else is not a repository without history.

Ask the repository first. `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md` or the README usually state
the convention outright, and that is more reliable than anything you infer.

Then look for where the work is actually written down. In rough order of how much they tell you:

- dated or numbered narrative records, wherever they live: `docs/sessions/`, `journal/`, `notes/`,
  `worklog/`, `.llm/`
- approved plans or designs: `docs/plans/`, `rfcs/`, `proposals/`, architecture decision records
  under `docs/adr/` or `docs/decisions/`
- a maintained `CHANGELOG.md`, which is a real timeline even though it is written for users
- checklists, roadmaps, handover notes and production-readiness documents
- briefs and deliverables from the client

Read the actual filenames rather than guessing the pattern. Padding and prefixes differ even
within one workspace: `session-01-…`, `session-1-…` and `2026-06-30-…` all appear. Exclude index
files.

### Fall back to git history, and treat it as a first-class source

When there are no written records, or they stop partway, **the commit history is the history**.
It is not a poor substitute; for most repositories it is the most accurate account that exists,
because it is the one nobody had to remember to write.

Read it properly rather than skimming the last few subjects:

- `git log --format='%ad %s' --date=short` for the whole arc, and notice where the clusters of
  activity are. Those clusters are usually the phases.
- Tags, releases and merge commits make natural phase boundaries where they exist.
- Commit subjects that read `feat(x):` or `fix(lms):` tell you what the thing is made of.
- The first and last commit dates give you the project's real start and its real state, which is
  often the single most valuable fact on the page.

Group related commits into periods of work and record those as the timeline. A project whose
history came from git rather than written records is a perfectly good project page.

**Say which source the timeline came from**, in the project description, so a reader knows how
complete it is. "Reconstructed from 175 commits; this repository keeps no session records" is
honest and useful. Implying a complete narrative you built from commit subjects is not.

And do not impose a convention on a repository that does not use one. If written records would
help here, propose it; do not quietly start a `docs/sessions/` folder in someone else's project.

### Then compare

In Linear, `list_issues` and `list_milestones` for the matched project.

Report under these headings, using whichever source of record this repository actually has:

```
Records on disk with no issue
Issues with no record behind them
Open issues the newest record shows are finished
Done issues with no milestone
Statements in the project description the repository contradicts
```

If every heading is empty and there is no new work, say so and move on. Do not manufacture
changes to make the run look productive.

## 5. Write it back

**For a new project**, follow the description shape in the team document, and set the
`Client:` label, the lead, the state, the start date from the first commit, and a priority.
Add links for the repository, the hosting dashboard, the CMS or database, and the live URL.
Project links are append-only through the MCP, so get them right the first time; a wrong one has
to be fixed by hand in the Resources panel.

**For an existing project**, change only what is wrong or missing:

- Correct statements the repository contradicts, and say in the description that it was corrected
  and why. A visible correction is worth more than a page that merely looks clean.
- Add the missing issues. Do not recreate issues that exist.
- Close what the newest session record shows is finished.
- Leave alone anything you cannot verify from the repository.

Then, for both:

1. **Plans.** Any approved plan with no issue gets one, created before execution, titled with the
   plan's title. A multi-session plan is a parent issue with one sub-issue per session.
2. **History.** Every unit of past work with no issue gets a Done issue in the right milestone,
   carrying its date, a short summary, and a link to wherever it is recorded. Where the repository
   keeps narrative records, that is one issue per record, titled `Session NN · <title>`. Where the
   history came from git, it is one issue per coherent piece of work rather than one per commit:
   title it after what changed, and link the commit range.
3. **What remains.** Each unresolved item in the newest record becomes an issue, or updates one
   that exists, in the right milestone, with a type label, area labels and a priority. Anything
   the client owes gets **Client action**, stays in Todo rather than In Progress, and says plainly
   who owes what.
4. **Phase gate.** If a milestone just reached 100%, post a project status update.

Where the repository keeps written records, write the issue identifier back into the file: 
`Linear: TEC-NN` on a plan's first line, and directly under a session record's H1. That
backreference is what makes the next reconciliation cheap. Skip it for repositories with no such
files; do not create files just to have somewhere to put the line.

Create ten or fewer issues per call, because larger batches time out. If a call returns a 502,
query before retrying: some of them land and only the response fails, so a blind retry duplicates
the issue.

## 6. Wire the repository

So the next person inherits this without being told. Skip whatever is already there.

1. A `## Linear` section in `CLAUDE.md` naming the project URL. Append to `AGENTS.md` instead
   where `CLAUDE.md` is a symlink to it.
2. A `.mcp.json` declaring `linear-server` at `https://mcp.linear.app/mcp`, added alongside any
   servers already present rather than replacing them.
3. This skill at `.claude/skills/linear-sync/SKILL.md`.

Commit with a Conventional Commit. Do not push unless asked.

## 7. Print the numbers

Finish with this, and do not report success without it:

```
Projects created / updated
Issues created / closed
Done issues vs records on disk, per project, naming which source the records came from
Done issues missing a milestone
Files edited and committed
```

If a number is wrong, say so. A run that reports a discrepancy is doing its job; one that rounds
it away has quietly become the problem it exists to prevent.

---

## A first run over many projects

A folder of projects, none of them in Linear yet. This could be five or it could be a hundred,
and the same command handles both.

**List what you found and wait.** Show every folder you intend to make a project for, and what
you are skipping and why. Let them strike things off before anything is written.

**Do the first project, then stop.** Complete it fully, then ask:

> That's the first one done. Would you like to look at it before I carry on, or should I continue
> through the rest?

Offer three answers: review it now, continue to the end, or stop here. The point of the pause is
that a misjudgement caught after one project is cheap and the same misjudgement caught after
ninety-nine is not. **Whatever they correct there applies to every remaining project.** Carry it
forward without being asked again.

**Then work through the rest one at a time**, committing each before starting the next, so an
interruption costs one project rather than all of them. Report a line or two per project, not an
essay.

**A rerun is safe and is the normal way to recover.** Check what already exists and continue from
there. Never start over, and never create a second project for a folder that already has one.

## Working alongside other people

Two people will run this against the same project, from different machines, on different parts of
the work. That must not turn into a fight over the same page.

- **Add, do not replace.** Create what is missing and leave the rest alone.
- **Re-read immediately before writing**, not from a listing taken several minutes ago. Someone
  may have changed it in between.
- **Do not close, reassign or rewrite an issue you did not create**, unless a session record in
  this repository shows the work is finished. Then name that record in the issue.
- **Do not overwrite a project description wholesale** when it holds material you cannot account
  for. Merge your correction into it instead.
- Session records are per-person and additive, so seeding history is naturally conflict-free.
  Trust that and leave other people's records alone.

If you find something that contradicts what a colleague wrote, do not quietly overwrite it.
Correct it, and say what changed and on what evidence.
