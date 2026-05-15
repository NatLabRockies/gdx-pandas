"""
md_to_rst.py
------------

Script to convert .md files to .rst. Expects to be passed a .txt file that lists
.md files using relative paths from its location and excluding the .md extension. 
Uses pandoc to convert those files to .rst, saving them in the same location and 
with the same filename, just with a different extension (.rst instead of .md). 
Then, if there is a corresponding .postfix file, the text in that file 
(assumed to be in .rst format) is appended to the resulting .rst.

:copyright: (c) 2026, Alliance for Energy Innovation, LLC
:license: BSD-3
"""

import argparse
import logging
import pathlib
from subprocess import call, list2cmdline

logger = logging.getLogger(__name__)


DEFAULT_LOG_FORMAT = '%(asctime)s|%(levelname)s|%(name)s|\n\t%(message)s'


def start_console_log(log_level=logging.WARN,log_format=DEFAULT_LOG_FORMAT):
    """
    Starts logging to the console.
    Parameters
    ----------
    log_level : enum
        logging package log level, i.e. logging.ERROR, logging.WARN, 
        logging.INFO or logging.DEBUG
    log_format : str
        format string to use with the logging package
    
    Returns
    -------
    logging.StreamHandler
        console_handler
    """
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    logformat = logging.Formatter(log_format)
    console_handler.setFormatter(logformat)
    logging.getLogger().setLevel(log_level)
    logging.getLogger().addHandler(console_handler)
    return console_handler    


def convert_files(file_registry):
    # registry is expected to contain paths relative to its location
    base_path = file_registry.parent

    if not file_registry.exists():
        raise ValueError(f"File registry {file_registry} not found")

    # loop through registry of md files
    with open(file_registry,'r') as registry:
        for line in registry:
            if line:
                # non-empty
                p = base_path / line.strip()
                p_md = p.parent / (p.stem + '.md')
                if not p_md.exists():
                    raise ValueError(f"There is no {p_md} file.")
                # run pandoc
                p_rst = p.parent / (p.stem + '.rst')
                try:
                    cmd_and_args = ['pandoc',str(p_md),'-o',str(p_rst)]
                    call(cmd_and_args)
                except Exception as e:
                    assert p_md.exists(), p_md
                    try:
                        call(['pandoc'])
                    except:
                        logger.error("Call to pandoc fails")
                        raise e
                    logger.error(f"Call '{list2cmdline(cmd_and_args)}' failed")
                    raise e
                assert p_rst.exists(), p_rst
                # append .postfix
                p_postfix = p.parent / (p.stem + '.postfix')
                if p_postfix.exists():
                    with open(p_rst, 'a') as rst:
                        rst.write("\n")
                        with open(p_postfix,'r') as postfix:
                            rst.write(postfix.read())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="""Utility to convert Markdown 
        (.md) files to reStructuredText (.rst)""")
    parser.add_argument('file_registry',
        help="""Text file that lists the markdown files to convert. Each line is 
        the file path and name for an .md file, where the path is relative to 
        the location of file_registry, and the .md extension is omitted.""",
        type=pathlib.Path)
    parser.add_argument("-d", "--debug", action='store_true', default=False,
        help="Option to output debug information.")
    
    args = parser.parse_args()

    # start logging
    start_console_log(log_level=logging.DEBUG if args.debug else logging.INFO)

    # perform conversion
    convert_files(args.file_registry)
