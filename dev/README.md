# Developer How-To

To get all of the development dependencies for Python:

```
pip install -e .[admin]
```

Also, you will need to install

- [pandoc](https://pandoc.org/installing.html)

## Create a new release

1. Update version number, CHANGES.txt, setup.py, LICENSE and header as needed
2. Run tests locally and fix any issues
3. Install from github and make sure tests pass 
4. Uninstall the draft package
5. Publish documentation
6. Create release on github
7. Release tagged version on pypi
    
## Publish documentation

The documentation is built with [Sphinx](http://sphinx-doc.org/index.html). There are several steps to creating and publishing the documentation:

1. Convert .md input files to .rst
2. Refresh API documentation
3. Build the HTML docs
4. Push to GitHub

### Markdown to reStructuredText

Markdown files are registered in `doc/source/md_files.txt`. Paths in that file should be relative to the docs folder and should exclude the file extension. For every file listed there, the `dev/md_to_rst.py` utility will expect to find a markdown (`.md`) file, and will look for an optional `.postfix` file, which is expected to contain `.rst` code to be appended to the `.rst` file created by converting the input `.md` file. Thus, running `dev/md_to_rst.py` on the `doc/source/md_files.txt` file will create revised `.rst` files, one for each entry listed in the registry. In summary:

```
cd doc/source
python ../../dev/md_to_rst.py md_files.txt
```

### Refresh API Documentation

- Make sure gdx-pandas is in your PYTHONPATH
- Delete the contents of `source/api`.
- Run `sphinx-apidoc -o source/api ..` from the `doc` folder.
- Compare `source/api/modules.rst` to `source/api.rst`. Delete `setup.rst` and references to it.
- Copy-paste the text in `gdxpds.postfix` at the bottom of `gdxpds.rst`
- 'git push' changes to the documentation source code as needed.
- Make the documentation per below

### Building HTML Docs

Run `make html` for Mac and Linux; `make.bat html` for Windows.

### Pushing to GitHub Pages

#### Mac/Linux

```
make github
```

#### Windows

```
make.bat html
```

Then run the github-related commands by hand:

```
git branch -D gh-pages
git push origin --delete gh-pages
ghp-import -n -b gh-pages -m "Update documentation" ./build/html
git checkout gh-pages
git push origin gh-pages
git checkout main # or whatever branch you were on
```

## Release on pypi

1. [using testpyi](https://packaging.python.org/guides/using-testpypi/) has good instructions for setting up your user account on TestPyPI and PyPI, and configuring twine to know how to access both repositories.
2. Test the package

    ```
    python setup.py sdist
    twine upload --repository testpypi dist/*
    # look at https://test.pypi.org/project/gdxpds/
    pip install --index-url https://test.pypi.org/simple/ gdxpds
    # check it out ... fix things ...
    ```

3. Upload to pypi

    ```
    twine upload --repository pypi dist/*
    ```
