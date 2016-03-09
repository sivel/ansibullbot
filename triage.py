#!/usr/bin/python
from __future__ import print_function

import argparse
import os
import sys
from datetime import datetime

from github import Github

from jinja2 import Environment, FileSystemLoader


loader = FileSystemLoader(os.path.join(os.path.dirname(__file__), 'templates'))
environment = Environment(loader=loader, trim_blocks=True)

ALIAS_LABELS = {
    'core_review': [
        'core_review_existing'
    ],
    'community_review': [
        'community_review_existing',
        'community_review_new'
    ],
    'shipit': [
        'shipit_owner_pr'
    ],
}

MAINTAINERS_FILES = {
    'core': "MAINTAINERS-CORE.txt",
    'extras': "MAINTAINERS-EXTRAS.txt",
}

# modules having files starting like the key, will get the value label
MODULE_NAMESPACE_LABELS = {
    'cloud': "cloud",
    'windows': "windows",
    'network': "networking"
}

# We don't remove any of these labels unless forced
SKIP_UNLABELING_FOR_LABELS = [
    "shipit",
    "needs_revision",
    "needs_info",
]

# Static labels, manually added
IGNORE_LABELS = [
    "feature_pull_request",
    "bugfix_pull_request",
    "in progress"
]

# We warn for human interaction
MANUAL_INTERACTION_LABELS = [
    "needs_revision",
    "needs_info",
]

BOTLIST = [
    'gregdek',
    'robynbergeron'
]


class PullRequest:

    def __init__(self, repo, pr_number=None, pr=None):
        self.repo = repo

        if not pr:
            self._pr = self.repo.get_pull(pr_number)
        else:
            self._pr = pr

        self.pr_number = self._pr.number

        self.pr_filenames = []
        self.current_pr_labels = []
        self.desired_pr_labels = []

        # we have a few labels we don't touch unless forced
        self.unlabeling_forced = False

        self.current_comments = []
        self.desired_comments = []

    def get_pr_filenames(self):
        if not self.pr_filenames:
            for pr_file in self._pr.get_files():
                self.pr_filenames.append(pr_file.filename)
        return self.pr_filenames

    def get_pr_submitter(self):
        return self._pr.user.login

    def pr_contains_new_file(self):
        for file in self._pr.get_files():
            if file.status == "added":
                return True
        return False

    def is_labeled_for_interaction(self):
        for current_pr_label in self.get_current_labels():
            if current_pr_label in MANUAL_INTERACTION_LABELS:
                return True
        return False

    def is_mergeable(self):
        return self._pr.mergeable_state != "dirty"

    def get_base_ref(self):
        return self._pr.base.ref

    def get_current_labels(self):
        """Pull the list of labels on this PR and shove them into
        pr_labels.
        """
        if not self.current_pr_labels:
            labels = self.repo.get_issue(self.pr_number).labels
            for label in labels:
                self.current_pr_labels.append(label.name)
        return self.current_pr_labels

    def get_comments(self):
        if not self.current_comments:
            self.current_comments = self._pr.get_issue_comments()
        return self.current_comments

    def add_desired_label(self, name=None):
        if name and name not in self.desired_pr_labels:
            self.desired_pr_labels.append(name)

    def add_desired_comment(self, boilerplate=None, maintainers=None):
        if not maintainers:
            maintainers = ['ansible/core']
        template = environment.get_template('%s.j2' % boilerplate)
        comment = template.render(maintainer=maintainers,
                                  submitter=self.get_pr_submitter())
        print(comment)
        if comment and comment not in self.desired_comments:
            self.desired_comments.append(comment)


class Triage:
    def __init__(self, verbose=None, github_user=None, github_pass=None,
                 github_token=None, github_repo=None, pr_number=None,
                 start_at_pr=None, always_pause=False):
        self.verbose = verbose
        self.github_user = github_user
        self.github_pass = github_pass
        self.github_token = github_token
        self.github_repo = github_repo
        self.pr_number = pr_number
        self.start_at_pr = start_at_pr
        self.always_pause = always_pause

        self.maintainers = {}

    def _connect(self):
        return Github(login_or_token=self.github_token or self.github_user,
                      password=self.github_pass)

    def _get_maintainers(self):
        if not self.maintainers:
            f = open(MAINTAINERS_FILES[self.github_repo])
            for line in f:
                owner_space = (line.split(': ')[0]).strip()
                maintainers_string = (line.split(': ')[-1]).strip()
                self.maintainers[owner_space] = maintainers_string.split(' ')
            f.close()
        return self.maintainers

    def init_actions(self):
        self.actions = {
            'newlabel': [],
            'unlabel':  [],
            'comments': [],
        }

    def debug(self, msg=""):
        if self.verbose:
            print("Debug: " + msg)

    def get_module_maintainers(self):
        module_maintainers = []
        # TODO: Make this simpler
        for owner_space, maintainers in self._get_maintainers().iteritems():
            for filename in self.pull_request.get_pr_filenames():
                if owner_space in filename:
                    for maintainer in maintainers:
                        if maintainer not in module_maintainers:
                            module_maintainers.extend(maintainers)
        return module_maintainers

    def add_desired_labels_for_not_mergeable(self):
        self.pull_request.unlabeling_forced = True
        self.pull_request.add_desired_label(name="needs_rebase")

    def add_desired_labels_by_namespace(self):
        for pr_filename in self.pull_request.get_pr_filenames():
            namespace = pr_filename.split('/')[0]
            for key, value in MODULE_NAMESPACE_LABELS.iteritems():
                if key == namespace:
                    self.pull_request.add_desired_label(value)

    def add_desired_labels_by_gitref(self):
        if "stable" in self.pull_request.get_base_ref():
            self.debug(msg="backport requested")
            self.pull_request.add_desired_label(name="core_review")
            self.pull_request.add_desired_label(name="backport")

    def add_desired_labels_by_maintainers(self):
        module_maintainers = self.get_module_maintainers()
        pr_contains_new_file = self.pull_request.pr_contains_new_file()

        if pr_contains_new_file:
            self.debug(msg="plugin is new")
            self.pull_request.add_desired_label(name="new_plugin")

        if "ansible" in module_maintainers:
            self.debug(msg="ansible in module maintainers")
            self.pull_request.add_desired_label(name="core_review_existing")
            return

        if "needs_revision" in self.pull_request.get_current_labels():
            self.debug(msg="needs revision labeled, skipping maintainer")
            return

        if self.pull_request.get_pr_submitter() in module_maintainers:
            self.debug(msg="plugin by owner, shipit as owner_pr")
            self.pull_request.add_desired_label(name="owner_pr")
            self.pull_request.add_desired_label(name="shipit_owner_pr")
            return

        if "shipit" in self.pull_request.get_current_labels():
            self.debug(msg="shipit labeled, skipping maintainer")
            return

        if not module_maintainers and pr_contains_new_file:
            self.debug(msg="New plugin, no module maintainer yet")
            self.pull_request.add_desired_label(name="community_review_new")
        else:
            self.debug(msg="existing plugin modified, module maintainer "
                           "should review")
            self.pull_request.add_desired_label(
                name="community_review_existing"
            )

    def process_comments(self):
        module_maintainers = self.get_module_maintainers()
        comments = self.pull_request.get_comments()

        self.debug("--- START Processing Comments:")

        for comment in comments:

            # Is the last useful comment from a bot user?  Then we've got a
            # potential timeout case. Let's explore!
            if comment.user.login in BOTLIST:

                self.debug("%s is in botlist: " % comment.user.login)

                today = datetime.today()
                time_delta = today - comment.created_at
                comment_days_old = time_delta.days

                self.debug("Days since last bot comment: %s" %
                           comment_days_old)

                if comment_days_old > 14:
                    pr_labels = self.pull_request.desired_pr_labels

                    if "core_review" in pr_labels:
                        self.debug("has core_review")
                        break

                    if "pending" not in comment.body:
                        if self.pull_request.is_labeled_for_interaction():
                            self.pull_request.add_desired_comment(
                                boilerplate="submitter_first_warning",
                                maintainers=module_maintainers
                            )
                            self.debug("submitter_first_warning")
                            break
                        if ("community_review" in pr_labels and not
                                self.pull_request.pr_contains_new_file()):
                            self.debug("maintainer_first_warning")
                            self.pull_request.add_desired_comment(
                                boilerplate="maintainer_first_warning",
                                maintainers=module_maintainers
                            )
                            break

                    # pending in comment.body
                    else:
                        if self.pull_request.is_labeled_for_interaction():
                            self.debug("submitter_second_warning")
                            self.pull_request.add_desired_comment(
                                boilerplate="submitter_second_warning",
                                maintainers=module_maintainers
                            )
                            self.pull_request.add_desired_label(
                                name="pending_action"
                            )
                            break
                        if ("community_review" in pr_labels and
                                "new_plugin" not in pr_labels):
                            self.debug("maintainer_second_warning")
                            self.pull_request.add_desired_comment(
                                boilerplate="maintainer_second_warning",
                                maintainers=module_maintainers
                            )
                            self.pull_request.add_desired_label(
                                name="pending_action"
                            )
                            break
                self.debug("STATUS: no useful state change since last pass "
                           "( %s )" % comment.user.login)
                break

            if comment.user.login in module_maintainers:
                self.debug("%s is module maintainer commented." %
                           comment.user.login)

                if "shipit" in comment.body:
                    self.debug("...said shipit!")
                    self.pull_request.unlabeling_forced = True
                    self.pull_request.add_desired_label(name="shipit")
                    self.pull_request.add_desired_comment(
                        boilerplate='shipit',
                        maintainers=module_maintainers
                    )
                    break

                elif "needs_revision" in comment.body:
                    self.debug("...said needs_revision!")
                    self.pull_request.unlabeling_forced = True
                    self.pull_request.add_desired_label(name="needs_revision")
                    break

            if comment.user.login == self.pull_request.get_pr_submitter():
                self.debug("%s is PR submitter commented." %
                           comment.user.login)
                if "ready_for_review" in comment.body:
                    self.debug("ready for review!")
                    self.pull_request.unlabeling_forced = True
                    if "ansible" in module_maintainers:
                        self.debug("core does the review!")
                        self.pull_request.add_desired_label(
                            name="core_review_existing"
                        )
                    elif not module_maintainers:
                        self.debug("community does the review!")
                        self.pull_request.add_desired_label(
                            name="community_review_new"
                        )
                    else:
                        self.debug("community does the review but has "
                                   "maintainer")
                        self.pull_request.add_desired_label(
                            name="community_review_existing"
                        )
                    break
        self.debug("--- END Processing Comments")

    def resolv_desired_pr_labels(self, desired_pr_label):
        """Resolves boilerplate the key labels to labels using an
        alias dict
        """
        for resolved_desired_pr_label, aliases in ALIAS_LABELS.iteritems():
            if desired_pr_label in aliases:
                return resolved_desired_pr_label
        return desired_pr_label

    def create_actions(self):
        """ Creates label unlabel and comment actions"""

        # create new label and comments action
        resolved_desired_pr_labels = []
        for desired_pr_label in self.pull_request.desired_pr_labels:

            # Most of the comments are only going to be added if we also add a
            # new label. So they are coupled. That is why we use the
            # boilerplate dict key as label and use an alias table containing
            # the real labels. This allows us to either use a real new label
            # without a comment or an label coupled with a comment. We check
            # if the label is a boilerplate dict key and get the real label
            # back or alternatively the label we gave as input
            # e.g. label: community_review_existing -> community_review
            # e.g. label: community_review -> community_review
            resolved_desired_pr_label = self.resolv_desired_pr_labels(
                desired_pr_label
            )

            # If we didn't get back the same, it means we must also add a
            # comment for this label
            if desired_pr_label != resolved_desired_pr_label:

                # we cache for later use in unlabeling actions
                resolved_desired_pr_labels.append(resolved_desired_pr_label)

                # We only add actions (newlabel, comments) if the label is
                # not already set
                if (resolved_desired_pr_label not in
                        self.pull_request.get_current_labels()):
                    # Use the previous label as key for the boilerplate dict
                    self.actions['comments'].append(desired_pr_label)
                    self.actions['newlabel'].append(resolved_desired_pr_label)

            # it is a real label, no comment needs to be added
            else:
                resolved_desired_pr_labels.append(desired_pr_label)
                if (desired_pr_label not in
                        self.pull_request.get_current_labels()):
                    self.actions['newlabel'].append(desired_pr_label)

        # unlabel action
        for current_pr_label in self.pull_request.get_current_labels():

            # some labels we just ignore
            if current_pr_label in IGNORE_LABELS:
                continue

            # some of them we ignore unless forced
            if (not self.pull_request.unlabeling_forced and
                    current_pr_label in SKIP_UNLABELING_FOR_LABELS):
                continue

            # now check if we need to unlabel
            if current_pr_label not in resolved_desired_pr_labels:
                self.actions['unlabel'].append(current_pr_label)

        # any manual desired comments we also take
        self.actions['comments'].extend(self.pull_request.desired_comments)

    def process(self):

        # clear all actions
        self.init_actions()

        # print some general infos about the PR to be processed
        print("\nPR #%s: %s" % (self.pull_request.pr_number,
                                self.pull_request._pr.title))
        print("Created at %s" % self.pull_request._pr.created_at)
        print("Updated at %s" % self.pull_request._pr.updated_at)

        # add desired labels
        if self.pull_request.is_mergeable():
            self.debug("PR is mergeable")
            self.add_desired_labels_by_namespace()
            self.add_desired_labels_by_gitref()
            self.add_desired_labels_by_maintainers()
            # process comments after labels
            self.process_comments()
        else:
            self.debug("PR is not mergeable")
            self.add_desired_labels_for_not_mergeable()

        self.create_actions()

        # Print the things we processed
        print("Submitter: %s" % self.pull_request.get_pr_submitter())
        print("Maintainers: %s" % ', '.join(self.get_module_maintainers()))
        print("Current Labels: %s" %
              ', '.join(self.pull_request.current_pr_labels))
        print("Actions: %s" % self.actions)

        if (self.actions['newlabel'] or self.actions['unlabel'] or
                self.actions['comments']):
            cont = raw_input("Take recommended actions (y/N/a)? ")
            if cont in ('a', 'A'):
                sys.exit(0)
            if cont in ('Y', 'y'):
                self.handle_action()
        elif self.always_pause:
            print("Skipping, but pause.")
            cont = raw_input("Continue (Y/n/a)? ")
            if cont in ('a', 'A', 'n', 'N'):
                sys.exit(0)
        else:
            print("Skipping.")

    def handle_action(self):
        pass

    def run(self):
        repo = self._connect().get_repo("ansible/ansible-modules-%s" %
                                        self.github_repo)

        if self.pr_number:
            self.pull_request = PullRequest(repo=repo,
                                            pr_number=self.pr_number)
            self.process()
        else:
            pulls = repo.get_pulls()
            for pull in pulls:
                if self.start_at_pr and pull.number > self.start_at_pr:
                    continue
                self.pull_request = PullRequest(repo=repo, pr=pull)
                self.process()


def main():
    parser = argparse.ArgumentParser(description="Triage various PR queues "
                                                 "for Ansible. (NOTE: only "
                                                 "useful if you have commit "
                                                 "access to the repo in "
                                                 "question.)")
    parser.add_argument("repo", type=str, choices=['core', 'extras'],
                        help="Repo to be triaged")
    parser.add_argument("--gh-user", "-u", type=str,
                        help="Github username or token of triager")
    parser.add_argument("--gh-pass", "-P", type=str,
                        help="Github password of triager")
    parser.add_argument("--gh-token", "-T", type=str,
                        help="Github token of triager")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")
    parser.add_argument("--debug", "-d", action="store_true",
                        help="Debug output")
    parser.add_argument("--pause", "-p", action="store_true",
                        help="Always pause between PRs")
    parser.add_argument("--pr", type=int,
                        help="Triage only the specified pr")
    parser.add_argument("--start-at", type=int,
                        help="Start triage at the specified pr")
    args = parser.parse_args()

    if args.pr and args.start_at:
        print("Error: Mutually exclusive: --start-at and --pr",
              file=sys.stderr)
        sys.exit(1)

    triage = Triage(
        verbose=args.verbose,
        github_user=args.gh_user,
        github_pass=args.gh_pass,
        github_token=args.gh_token,
        github_repo=args.repo,
        pr_number=args.pr,
        start_at_pr=args.start_at,
        always_pause=args.pause,
    )
    triage.run()

if __name__ == "__main__":
    main()
