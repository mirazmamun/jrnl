#!/usr/bin/env python

"""
    jrnl

    license: GPLv3, see LICENSE.md for more details.

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import logging
import packaging.version
import platform
import sys

from . import install, plugins, util
from .parse_args import parse_args
from .Journal import PlainJournal, open_journal
from .util import ERROR_COLOR, RESET_COLOR, UserAbort
from .util import get_journal_name

log = logging.getLogger(__name__)
logging.getLogger("keyring.backend").setLevel(logging.ERROR)


def guess_mode(args, config):
    """Guesses the mode (compose, read or export) from the given arguments"""
    compose = True
    export = False
    if (
        args.decrypt is not False
        or args.encrypt is not False
        or args.export is not False
        or any((args.short, args.tags, args.edit, args.delete))
    ):
        compose = False
        export = True
    elif any(
        (
            args.start_date,
            args.end_date,
            args.on_date,
            args.limit,
            args.strict,
            args.starred,
            args.contains,
        )
    ):
        # Any sign of displaying stuff?
        compose = False
    elif args.text and all(
        word[0] in config["tagsymbols"] for word in " ".join(args.text).split()
    ):
        # No date and only tags?
        compose = False

    return compose, export


def encrypt(journal, filename=None):
    """ Encrypt into new file. If filename is not set, we encrypt the journal file itself. """
    from .EncryptedJournal import EncryptedJournal

    journal.config["encrypt"] = True

    new_journal = EncryptedJournal.from_journal(journal)
    new_journal.write(filename)

    print(
        "Journal encrypted to {}.".format(filename or new_journal.config["journal"]),
        file=sys.stderr,
    )


def decrypt(journal, filename=None):
    """ Decrypts into new file. If filename is not set, we encrypt the journal file itself. """
    journal.config["encrypt"] = False

    new_journal = PlainJournal.from_journal(journal)
    new_journal.write(filename)
    print(
        "Journal decrypted to {}.".format(filename or new_journal.config["journal"]),
        file=sys.stderr,
    )


def update_config(config, new_config, scope, force_local=False):
    """Updates a config dict with new values - either global if scope is None
    or config['journals'][scope] is just a string pointing to a journal file,
    or within the scope"""
    if scope and type(config["journals"][scope]) is dict:  # Update to journal specific
        config["journals"][scope].update(new_config)
    elif scope and force_local:  # Convert to dict
        config["journals"][scope] = {"journal": config["journals"][scope]}
        config["journals"][scope].update(new_config)
    else:
        config.update(new_config)


def configure_logger(debug=False):
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.ERROR,
        format="%(levelname)-8s %(name)-12s %(message)s",
    )
    logging.getLogger("parsedatetime").setLevel(
        logging.INFO
    )  # disable parsedatetime debug logging


def run(manual_args=None):
    if packaging.version.parse(platform.python_version()) < packaging.version.parse(
        "3.7"
    ):
        print(
            f"""{ERROR_COLOR}
ERROR: Python version {platform.python_version()} not supported.

Please update to Python 3.7 (or higher) in order to use jrnl.
{RESET_COLOR}""",
            file=sys.stderr,
        )
        sys.exit(1)

    if manual_args is None:
        manual_args = sys.argv[1:]

    args = parse_args(manual_args)
    configure_logger(args.debug)

    # Run command if possible before config is available
    if callable(args.preconfig_cmd):
        args.preconfig_cmd(args)
        sys.exit(0)

    # Load the config
    try:
        config = install.load_or_install_jrnl()
        original_config = config.copy()
        args = get_journal_name(args, config)
        config = util.scope_config(config, args.journal_name)
    except UserAbort as err:
        print(f"\n{err}", file=sys.stderr)
        sys.exit(1)

    # Run post-config command now that config is ready
    if callable(args.postconfig_cmd):
        args.postconfig_cmd(args=args, config=config)
        sys.exit(0)

    # --- All the standalone commands are now done --- #

    # Get the journal we're going to be working with
    journal = open_journal(args.journal_name, config)

    mode_compose, mode_export = guess_mode(args, config)

    if mode_compose and not args.text:
        if not sys.stdin.isatty():
            # Piping data into jrnl
            raw = sys.stdin.read()
        elif config["editor"]:
            template = ""
            if config["template"]:
                try:
                    template = open(config["template"]).read()
                except OSError:
                    print(
                        f"[Could not read template at '{config['template']}']",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            raw = util.get_text_from_editor(config, template)
        else:
            try:
                _how_to_quit = (
                    "Ctrl+z and then Enter" if "win32" in sys.platform else "Ctrl+d"
                )
                print(
                    f"[Writing Entry; on a blank line, press {_how_to_quit} to finish writing]\n",
                    file=sys.stderr,
                )
                raw = sys.stdin.read()
            except KeyboardInterrupt:
                print("[Entry NOT saved to journal]", file=sys.stderr)
                sys.exit(0)
        if raw:
            args.text = [raw]
        else:
            sys.exit()

    # Writing mode
    if mode_compose:
        raw = " ".join(args.text).strip()
        log.debug('Appending raw line "%s" to journal "%s"', raw, args.journal_name)
        journal.new_entry(raw)
        print(f"[Entry added to {args.journal_name} journal]", file=sys.stderr)
        journal.write()

    if not mode_compose:
        old_entries = journal.entries
        if args.on_date:
            args.start_date = args.end_date = args.on_date
        journal.filter(
            tags=args.text,
            start_date=args.start_date,
            end_date=args.end_date,
            strict=args.strict,
            starred=args.starred,
            exclude=args.excluded,
            contains=args.contains,
        )
        journal.limit(args.limit)

    # Reading mode
    if not mode_compose and not mode_export:
        print(journal.pprint())

    # Various export modes
    elif args.short:
        print(journal.pprint(short=True))

    elif args.tags:
        print(plugins.get_exporter("tags").export(journal))

    elif args.export is not False:
        exporter = plugins.get_exporter(args.export)
        print(exporter.export(journal, args.output))

    elif args.encrypt is not False:
        encrypt(journal, filename=args.encrypt)
        # Not encrypting to a separate file: update config!
        if not args.encrypt:
            update_config(
                original_config, {"encrypt": True}, args.journal_name, force_local=True
            )
            install.save_config(original_config)

    elif args.decrypt is not False:
        decrypt(journal, filename=args.decrypt)
        # Not decrypting to a separate file: update config!
        if not args.decrypt:
            update_config(
                original_config, {"encrypt": False}, args.journal_name, force_local=True
            )
            install.save_config(original_config)

    elif args.edit:
        if not config["editor"]:
            print(
                "[{1}ERROR{2}: You need to specify an editor in {0} to use the --edit function.]".format(
                    install.CONFIG_FILE_PATH, ERROR_COLOR, RESET_COLOR
                ),
                file=sys.stderr,
            )
            sys.exit(1)
        other_entries = [e for e in old_entries if e not in journal.entries]
        # Edit
        old_num_entries = len(journal)
        edited = util.get_text_from_editor(config, journal.editable_str())
        journal.parse_editable_str(edited)
        num_deleted = old_num_entries - len(journal)
        num_edited = len([e for e in journal.entries if e.modified])
        prompts = []
        if num_deleted:
            prompts.append(
                "{} {} deleted".format(
                    num_deleted, "entry" if num_deleted == 1 else "entries"
                )
            )
        if num_edited:
            prompts.append(
                "{} {} modified".format(
                    num_edited, "entry" if num_deleted == 1 else "entries"
                )
            )
        if prompts:
            print("[{}]".format(", ".join(prompts).capitalize()), file=sys.stderr)
        journal.entries += other_entries
        journal.sort()
        journal.write()

    elif args.delete:
        if journal.entries:
            entries_to_delete = journal.prompt_delete_entries()

            if entries_to_delete:
                journal.entries = old_entries
                journal.delete_entries(entries_to_delete)

                journal.write()
        else:
            print(
                "No entries deleted, because the search returned no results.",
                file=sys.stderr,
            )
