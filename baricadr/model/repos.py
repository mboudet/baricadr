import datetime
import fnmatch
import os
import tempfile
import time

from baricadr.db_models import BaricadrTask

from flask import current_app

import yaml


class Repo():

    def __init__(self, local_path, conf):

        if 'backend' not in conf:
            raise ValueError("Malformed repository definition, missing backend '%s'" % conf)

        self.local_path = local_path  # No trailing slash

        perms = self._check_perms()
        if not perms['writable']:
            raise ValueError("Path '%s' is not writable" % local_path)

        self.exclude = None
        if 'exclude' in conf:
            self.exclude = conf['exclude']
        self.conf = conf
        self.freeze_age = 180
        if 'freeze_age' in conf:
            try:
                conf['freeze_age'] = int(conf['freeze_age'])
            except ValueError:
                raise ValueError("Malformed repository definition, freeze_age must be an integer in '%s'" % conf)

            if conf['freeze_age'] < 2 or conf['freeze_age'] > 10000:
                raise ValueError("Malformed repository definition, freeze_age must be an integer >1 and <10000 in '%s'" % conf)

            self.freeze_age = conf['freeze_age']

        # TODO [HI] allow using non-freezable repos = only allow pulls -> must be specified in conf?
        if not perms['freezable']:
            raise ValueError("Malformed repository definition for local path '%s', this path does not support atime" % local_path)

        self.backend = current_app.backends.get_by_name(conf['backend'], conf)

    def is_in_repo(self, path):
        path = os.path.join(path, "")

        return path.startswith(os.path.join(self.local_path, ""))

    def pull(self, path):
        return self.backend.pull(self, path)

    def remote_is_single(self, path):
        return self.backend.remote_is_single(self, path)

    def relative_path(self, path):
        return path[len(self.local_path) + 1:]

    def remote_list(self, path, missing=False, max_depth=1, from_root=False):
        """
        List files from remote repository

        :type path: str
        :param path: Path where baricadr should list files

        :type missing: bool
        :param missing: Only list files missing from the local path

        :type from_root: bool
        :param from_root: Return full paths from root of the repo (instead of relative to given path)

        :type max_depth: int
        :param max_depth: Restrict to a max depth. Set to 0 for all files.

        :rtype: list
        :return: list of files
        """

        return self.backend.remote_list(self, path, missing, max_depth, from_root)

    def freeze(self, path, force=False, dry_run=False):
        """
        Remove files from local repository

        :type path: str
        :param path: Path where baricadr should freeze files

        :type force: bool
        :param force: Force freezing path, even if files were accessed recently.

        :type dry_run: bool
        :param dry_run: Do not remove anything, just print what would be done in normal mode.

        :rtype: list
        :return: list of freezed files
        """

        # TODO [LOW] keep track of md5 if needed for checking
        # TODO [LOW] check rclone check -> does it work without hash support with sftp in rclone?
        # TODO [HI] test force mode in freeze

        current_app.logger.info("Asked to freeze '%s'" % path)

        remote_list = self.remote_list(path, max_depth=0, from_root=True)

        freezables = self._get_freezable(path, remote_list, force)

        current_app.logger.info("Freezable files: %s" % freezables)

        for to_freeze in freezables:
            if dry_run:
                current_app.logger.info("Would freeze '%s' (dry-run mode)" % (to_freeze))
            else:
                current_app.logger.info("Freezing '%s'" % (to_freeze))
                self._do_freeze(to_freeze)

        return freezables

    # Might actually use this to run safety checks (can_write? others?)
    def _check_perms(self):
        if not current_app.is_worker:
            # The web app doesn't need to have write access, nor to check if the repo is freezable
            # The web forker thread is "nginx", not root, so it cannot write anyway.
            current_app.logger.info("Web process, skipping perms checks for repo %s" % (self.local_path))
            return {"writable": True, "freezable": True}

        perms = {"writable": True, "freezable": False}
        try:
            # TODO [HIHI] this fails in docker tests
            with tempfile.NamedTemporaryFile(dir=self.local_path) as test_file:
                starting_atime = os.stat(test_file.name).st_atime
                # Need to wait a bit
                time.sleep(0.5)
                test_file.read()
                if not os.stat(test_file.name).st_atime == starting_atime:
                    perms["freezable"] = True
        except OSError:
            perms["writable"] = False

        current_app.logger.info("Worker process, perms detected for repo %s: %s" % (self.local_path, perms))

        return perms

    def _get_freezable(self, path, remote_list, force=False):
        freezables = []

        excludes = []
        if self.exclude:
            excludes = self.exclude.split(',')

        if os.path.exists(path) and os.path.isfile(path):
            for ex in excludes:
                if fnmatch.fnmatch(path, ex.strip()):
                    current_app.logger.info("Found excluded path: %s with expression %s" % (path, ex.strip()))
                    return
            if (force or self._can_freeze(path)) and (self.relative_path(path) in remote_list):
                freezables.append(path)
        else:
            for root, subdirs, files in os.walk(path):
                for name in files:
                    candidate = os.path.join(root, name)
                    current_app.logger.info("Evaluating freezable for path: %s -> force %s can_freeze %s in remote_list %s" % (candidate, force, self._can_freeze(path), (self.relative_path(path) in remote_list)))
                    excluded = False
                    for ex in excludes:
                        if fnmatch.fnmatch(candidate, ex.strip()):
                            current_app.logger.info("Found excluded path: %s with expression %s" % (candidate, ex.strip()))
                            excluded = True
                            break
                    if not excluded and (force or self._can_freeze(candidate)) and (self.relative_path(candidate) in remote_list):
                        freezables.append(candidate)

        return freezables

    def _can_freeze(self, file_to_check):
        """
        Check if a file should be freezed or not

        :type path: str
        :param path: Path of a file to check

        :rtype: bool
        :return: True if the file should be freezed
        """

        last_access = datetime.datetime.fromtimestamp(os.stat(file_to_check).st_atime).date()
        now = datetime.date.today()
        delta = now - last_access
        delta = delta.days
        current_app.logger.info("Checking if we should freeze '%s' (freeze_age=%s): last accessed on %s (%s days ago) =>  %s" % (file_to_check, self.freeze_age, last_access, delta, delta > self.freeze_age))

        return delta > self.freeze_age

    def _do_freeze(self, file_to_freeze):
        """
        Removes a cold file from local repository

        :type path: str
        :param path: Path of a file to freeze
        """

        os.unlink(file_to_freeze)


class Repos():

    def __init__(self, config_file, backends):

        self.config_file = config_file
        self.backends = backends

        self.read_conf(config_file)

    def read_conf(self, path):

        with open(path, 'r') as stream:
            self.repos = self.do_read_conf(stream.read())

    def read_conf_from_str(self, content):

        self.repos = self.do_read_conf(content)

    def do_read_conf(self, content):

        repos = {}
        repos_conf = yaml.safe_load(content)
        if not repos_conf:
            raise ValueError("Malformed repository definition '%s'" % content)

        for repo in repos_conf:
            # We use realpath instead of abspath to resolve symlinks and be sure the user is not doing strange things
            repo_abs = os.path.realpath(repo)
            if not os.path.exists(repo_abs):
                current_app.logger.warning("Directory '%s' does not exist, creating it" % repo_abs)
                os.makedirs(repo_abs)
            if repo_abs in repos:
                raise ValueError('Could not load duplicate repository for path "%s"' % repo_abs)

            for known in repos:
                if self._is_subdir_of(repo_abs, known):
                    raise ValueError('Could not load repository for path "%s", conflicting with "%s"' % (repo_abs, known))

            repos[repo_abs] = Repo(repo_abs, repos_conf[repo])

        return repos

    def _is_subdir_of(self, path1, path2):

        path1 = os.path.join(path1, "")
        path2 = os.path.join(path2, "")

        if path1 == path2:
            return True

        if len(path1) > len(path2):
            if path2 == path1[:len(path2)]:
                return True
        elif len(path1) < len(path2):
            if path1 == path2[:len(path1)]:
                return True

        return False

    def get_repo(self, path):

        path = os.path.join(path, "")

        for repo in self.repos:
            if self.repos[repo].is_in_repo(path):
                return self.repos[repo]

        raise RuntimeError('Could not find baricadr repository for path "%s"' % path)

    def is_already_touching(self, path):
        """
        If a task is already pulling/freezing path or an upper directory, returns the task id.
        Return False otherwise.
        """

        running_tasks = BaricadrTask.query.all()
        for rt in running_tasks:
            if rt.finished is None and path.startswith(rt.path):
                return rt.task_id

        return False

    def is_locked_by_subdir(self, path):
        """
        If some tasks are already pulling/freezing a subdirectory of path, returns the list of task ids.
        Return an empty list otherwise.
        """

        running_tasks = BaricadrTask.query.all()
        locking = []
        for rt in running_tasks:
            if rt.finished is None and rt.path.startswith(path) and path != rt.path:
                locking.append(rt.task_id)

        return locking
