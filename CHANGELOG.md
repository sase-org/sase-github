# Changelog

## [0.1.4](https://github.com/sase-org/sase-github/compare/v0.1.3...v0.1.4) (2026-06-30)


### Bug Fixes

* require current sase package ([61c3e34](https://github.com/sase-org/sase-github/commit/61c3e345c6832c9575694951e1337ef4f3c24aaf))

## [0.1.3](https://github.com/sase-org/sase-github/compare/v0.1.2...v0.1.3) (2026-06-29)


### Features

* write project display names for repo refs ([65ddc1d](https://github.com/sase-org/sase-github/commit/65ddc1d6dd9efe152897449ffa7eb421d8694802))


### Documentation

* add tiny field note ([d1d6563](https://github.com/sase-org/sase-github/commit/d1d6563822f3e48ec2504fed9156d1d0bbabb8a1))

## [0.1.2](https://github.com/sase-org/sase-github/compare/v0.1.1...v0.1.2) (2026-06-13)


### Bug Fixes

* restore sase-github PyPI publish workflow ([077e91a](https://github.com/sase-org/sase-github/commit/077e91a57e836856d1e263cdc348cf3761516b4c))
* use trusted publisher workflow path ([6a9fea0](https://github.com/sase-org/sase-github/commit/6a9fea00701428903f086ac871ac6c103521ae1d))


### Documentation

* add PyPI version badge ([58f0145](https://github.com/sase-org/sase-github/commit/58f014514664d6c8402b04bbaea37e0402099312))

## 0.1.1 (2026-06-09)


### Features

* Add #prdd xprompt tagged with append_to_commit_and_propose ([cc29edd](https://github.com/sase-org/sase-github/commit/cc29eddaaad0e9dbdef90e045dc8e3bb046e7dbd))
* Add can_rename_branch override returning False ([f60d739](https://github.com/sase-org/sase-github/commit/f60d739b5278a625c39972aab748b736775a6d20))
* Add default_config.yml with pr_diff xprompt and sase_config entry point ([d1e9592](https://github.com/sase-org/sase-github/commit/d1e9592fb5f81954335b90d7747931c262f440cb))
* add descriptions to GitHub xprompts (sase-3w.5) ([ceb2c51](https://github.com/sase-org/sase-github/commit/ceb2c51bdd491a09c4128507fb6e6ee5e2772375))
* Add finally: true to release and diff steps in gh workflow ([3b7786e](https://github.com/sase-org/sase-github/commit/3b7786e734f9b256f745435daf78b29f297eb9a4))
* Add GitHubWorkspacePlugin and migrate scripts from sase core ([b6e1499](https://github.com/sase-org/sase-github/commit/b6e1499de165a6d0b22bbb39207e4ae913ee7954))
* Add LLM provider model name and agent name to PR description ([8a22569](https://github.com/sase-org/sase-github/commit/8a225697ba94bc23e6b78361d8ce371d693899e5))
* Add post-cleanup git pull to #gh xprompt workflow ([96bdc31](https://github.com/sase-org/sase-github/commit/96bdc31ad241f8a505055b3a8e2fc524cffb265d))
* Add rollover tag to gh VCS xprompt workflow ([661200c](https://github.com/sase-org/sase-github/commit/661200c32b0a9d724d98d006b36d88518c71b4e0))
* Add VCS check hooks for GitHub workspace plugin ([cd43132](https://github.com/sase-org/sase-github/commit/cd43132898038b96150a68b2216d30afcae8a696))
* Add VCS commit dispatch hooks (vcs_create_commit, vcs_create_proposal, vcs_create_pull_request) with tests (sase-a.1) ([1147d77](https://github.com/sase-org/sase-github/commit/1147d77ad77113c85feba7be3d17a0aa0fca3b10))
* Add vcs_classify_repo hook to claim repos with github.com URLs ([5098353](https://github.com/sase-org/sase-github/commit/50983531c226fb7d6977b5b1fe526cba11b8f473))
* Add vcs_get_change_body hook implementation ([d264a57](https://github.com/sase-org/sase-github/commit/d264a572657cd2a95e2858bf1872f13b1894377d))
* Add workflow_label to #gh xprompt for descriptive RUNNING field names ([3020428](https://github.com/sase-org/sase-github/commit/3020428201e0714052f9ac51e3b5e2972b2bd102))
* Add wraps_all flag to gh xprompt (sase-4.2) ([fd56d9c](https://github.com/sase-org/sase-github/commit/fd56d9cf7114fb763e07d8b05a8172b9ee355ef2))
* Add ws_prepare_mail hookimpl for GitHub git mail prep ([8865801](https://github.com/sase-org/sase-github/commit/886580159a80c5fba6a285975459547e6987b52d))
* allocate GitHub project aliases on first use (sase-4d.2) ([3fa99c8](https://github.com/sase-org/sase-github/commit/3fa99c8cc44825581c3ecd8a281402d2d9a087e2))
* Allow slash as VCS ref separator in #gh pattern ([e126779](https://github.com/sase-org/sase-github/commit/e126779256305df3e22cef8fb804f22899aa8111))
* Allow underscore as VCS ref separator (#gh_sase = #gh:sase) ([46aaabf](https://github.com/sase-org/sase-github/commit/46aaabf7ccfb7a75c085cc3ef4a381a97c09e487))
* Create %gh runner provider (%w:sase-yyyx.4) ([db2878d](https://github.com/sase-org/sase-github/commit/db2878d7af28c9b80d25c796fd5986cd3d079566))
* Data-Driven VCS Family Mapping + running_field.py Cleanup ([7128545](https://github.com/sase-org/sase-github/commit/7128545b5f681efb31b3df6f2828d900d4ac0190))
* Fix #pr branch naming and #gh checkout for project names ([f2c84dc](https://github.com/sase-org/sase-github/commit/f2c84dcb9b526e477f187869de8e481af7c65370))
* Implement vcs_abandon_change hook to close PRs during revert/archive (sase-c.2) ([94f3902](https://github.com/sase-org/sase-github/commit/94f3902b3432ff0d308ea076399ea520cffbbc9a))
* Implement ws_get_workflow_metadata hook for GitHub plugin ([39d6513](https://github.com/sase-org/sase-github/commit/39d65139c107fd7205232d20a765608f7e5de1ed))
* Implement ws_get_workspace_directory and ws_format_commit_description hooks ([393e77d](https://github.com/sase-org/sase-github/commit/393e77d21eaa4b1f1402a6d6a77e89697dfb3cd1))
* Inherit some VCS hooks from the common git impl ([879cd47](https://github.com/sase-org/sase-github/commit/879cd475549281c5a3c667d27de0a284ced9531f))
* Initial sase-github plugin package ([5372725](https://github.com/sase-org/sase-github/commit/537272566798840ec725169ecf676838b630854e))
* Make #pr fail fast when not on right branch ([eb475ba](https://github.com/sase-org/sase-github/commit/eb475baf24a86a522ded541f8911519a6f6a6dc6))
* prefer .sase project spec extension with legacy .gp fallback (sase-33.5a) (sase-33) ([9340d36](https://github.com/sase-org/sase-github/commit/9340d3676f0c15bd6b67dfa8d4b9da2372523e7a))
* Replace github_username with github_orgs list config ([11420cf](https://github.com/sase-org/sase-github/commit/11420cfbdc1b0c65601c11a6ca77b9eba3f05b3c))
* Skip prdd xprompt expansion on default branch ([add1609](https://github.com/sase-org/sase-github/commit/add160966c8803a8af6c278f701fa53a6d1d329b))
* Support use_project_pr_prefix config for PR title prefixing ([21a76b7](https://github.com/sase-org/sase-github/commit/21a76b7d2fe04c5bef523dd585062ad459d89619))
* Use _pr_body payload field for PR description body ([e3f48c7](https://github.com/sase-org/sase-github/commit/e3f48c75ed3dbf133f588ba763491a1e50ee7972))
* Use commit message body as PR description when available ([ecabacd](https://github.com/sase-org/sase-github/commit/ecabacd1ae5797475379fa21b2e41b60658054ab))


### Bug Fixes

* #pr_diff showing unrelated file changes ([1c7cc23](https://github.com/sase-org/sase-github/commit/1c7cc23b9c219102fde9e30d867f0113a13c6708))
* Add explicit git push and robust URL extraction to #pr workflow ([b4da722](https://github.com/sase-org/sase-github/commit/b4da7226372b0722b93b0faf57eaa717bb5308c1))
* Add vcs_provider_name='github' to workspace plugin metadata ([05f5f1e](https://github.com/sase-org/sase-github/commit/05f5f1e18e64232c69ab4f87ed7ea130b80d3729))
* Checkout CL branch in #gh workflow before agent runs ([32163e0](https://github.com/sase-org/sase-github/commit/32163e0298a83e424b3ceb6e07aa933a0281e4ba))
* Emit meta_new_pr output so PR URL appears in ace TUI metadata panel ([83de875](https://github.com/sase-org/sase-github/commit/83de8753bbf3557b8c647fcbcdb69196dacff056))
* Fix PR description footer newline between Model and Agent lines ([087f9fd](https://github.com/sase-org/sase-github/commit/087f9fdd53356d22e1a011b3307229b5733510b0))
* Handle existing branch in #pr workflow create_branch step ([d5a212a](https://github.com/sase-org/sase-github/commit/d5a212a469a61ad7bbbd02f8681501ced90cbc50))
* Handle remote branch already existing in pr create_branch step ([b3a8196](https://github.com/sase-org/sase-github/commit/b3a81964c39b2e4c20a8aa87c6d28aec04f641c3))
* Inline pr_diff logic into prdd.yml so #pr_diff expands correctly ([28c428c](https://github.com/sase-org/sase-github/commit/28c428c3445a962d6682b1cd2fc652542896cae3))
* Make #prdd not show when not in a git repo ([8742394](https://github.com/sase-org/sase-github/commit/87423942a35fd90599c7f8908717e83c5b42bca4))
* Remove pipefail from #pr create_branch step (incompatible with dash) ([fceee3e](https://github.com/sase-org/sase-github/commit/fceee3e8ae7441c30a6dd756783ac63a286e0e55))
* Stash and pull as pre-step ([bbd8488](https://github.com/sase-org/sase-github/commit/bbd8488648d213059b88d0d9a1f2b50f7b656565))
* update callers for removed ensure_git_clone wrapper ([8a21fc2](https://github.com/sase-org/sase-github/commit/8a21fc2e63a4b8306552f1059746ac814d7864b3))
* Update stale test mocks for commit, proposal, and PR tests ([2bee80d](https://github.com/sase-org/sase-github/commit/2bee80d81bb37bb2fa9322d90684d9b2ceda073a))
* Update test to match new branch-reuse behavior in #pr workflow ([1ef7569](https://github.com/sase-org/sase-github/commit/1ef7569ce56833c8bb4b482b78dd21108442fb15))
* Use comma separator for multi-value xprompt tags in gh.yml ([1204d2b](https://github.com/sase-org/sase-github/commit/1204d2b95ab3ea302e5f5687eff2296dd0c2d47f))
* Use parent PID in gh_setup to prevent stale RUNNING entry cleanup ([09d0703](https://github.com/sase-org/sase-github/commit/09d07039ed59028d1dc4230b582bbb5ae21c2bfd))
* Use recorded PR URL for submit instead of branch heuristics ([e0f5013](https://github.com/sase-org/sase-github/commit/e0f5013636df527f9607bd6a269df606f8319788))
