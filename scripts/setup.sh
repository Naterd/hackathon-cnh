#!/usr/bin/env bash
# Point this repo at your team's namespace and your GitHub repo.
#   ./scripts/setup.sh <team-name>
# The team name is the one the Radar table gave you: your namespace is
# team-<team-name> and your URL is https://<team-name>.cnh.radarhq.io.
set -euo pipefail

team="${1:-}"
if [[ ! "$team" =~ ^[a-z0-9]([a-z0-9-]{0,18}[a-z0-9])?$ ]]; then
  echo "usage: $0 <team-name>   (lowercase letters, digits and dashes, up to 20 characters)" >&2
  exit 1
fi

remote="$(git remote get-url origin 2>/dev/null || true)"
repo="$(printf '%s' "$remote" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')"
if [[ ! "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "Couldn't read your GitHub repo from 'git remote get-url origin' (got: ${remote:-nothing})." >&2
  echo "Create your repo from the template on GitHub, clone it, and run this inside it." >&2
  exit 1
fi
repo_lower="$(printf '%s' "$repo" | tr '[:upper:]' '[:lower:]')"
if [[ "$repo_lower" == skyhook-io/cnh-starter ]]; then
  echo "This is a clone of the template itself. Click Use this template on GitHub to make your own" >&2
  echo "public copy, clone that, and run this inside it." >&2
  exit 1
fi

files=$(grep -rl -e __TEAM__ -e OWNER/REPO deploy argocd examples AGENTS.md || true)
for f in $files; do
  if [ "$f" = deploy/kustomization.yaml ]; then
    # The image lines here belong to the GitHub Action, which sets them on every
    # push. Leaving them alone keeps your `git pull --rebase` from conflicting
    # with the deploy commit the Action makes when GitHub creates your repo.
    sed -i.bak -e "s#__TEAM__#${team}#g" "$f"
  else
    # GHCR image names must be lowercase; the git URL keeps its case.
    sed -i.bak -e "s#ghcr.io/OWNER/REPO#ghcr.io/${repo_lower}#g" \
               -e "s#OWNER/REPO#${repo}#g" \
               -e "s#__TEAM__#${team}#g" "$f"
  fi
  rm -f "$f.bak"
done

cat <<EOF
Done. Your cluster is ${team} in Radar Cloud (the app runs in namespace team-${team} inside it),
and your URL is https://${team}.cnh.radarhq.io.

Next:
  1. git commit -am "Set up team ${team}" && git pull --rebase && git push
     The GitHub Action builds your image and pushes it to ghcr.io/${repo_lower}.
     Wait for it to go green (the repo's Actions tab).
  2. Once the first build finishes, check the image is public: on your repo's GitHub page, open
     Packages (right-hand column) and your image. It usually already says Public. If it says
     Private, open Package settings > Change visibility > Public: the cluster can't pull a private one.
  3. Connect your agent to Radar, once. For Claude Code:
       claude mcp add --scope user radar-cloud --transport http https://api.radarhq.io/mcp
     Then in Claude Code run /mcp, pick radar-cloud and Authenticate, and choose
     Cloud Native Hackathon under "Organization access".
  4. Start your agent in this folder and ask it:
       Get me set up for the hackathon. Our team is ${team}.
     It starts GitOps through Radar and checks https://${team}.cnh.radarhq.io shows your commit.
EOF
