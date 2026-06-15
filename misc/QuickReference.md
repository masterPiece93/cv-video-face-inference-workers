# Quick Reference

1. how docker files of each worker uses common requirements and personal requirements

    - in the common module , each service is having it's own requirements file
        - there is a requirements folder , & it containes requirements file per python version
    - in each worker's docker file , we explicity copy this file , and install the dependencies .
    - this approach is completely manual , the developer have to select the common service that is being used by the sepecific worker , and hence copy & referece that particular requirements file in Dockerfile.
    - each worker have it's own requirements file as well , it is copied & referenced as well in the Dockerfile .
    - **NOTE** : It can happen that for a particular python version , the common service and the worker codebase have dependency on a same python package/library .
        - in such a case , always resolve and have a common version number for a package/library ( in both common service & worker ).
        - also remember that , the common module is used across all the 3 workers , so package/library implementation shall be in sync with all the three .

2. what is the use of `common_requirements` folder 

    - it is used just for the purpose of listing out the common requirements across all the workers .

3. how run tests for this codebase
    - install the `requirements-dev.txt` ( preferabley in a new seperate venv )
    - refer the [README](./gta-ai-eci-workers/tests/README.md) in `tests/` folder at root . It is enough to guide you for executing all the tests .


---

## SPECIAL NOTES

- **all unit tests must pass, if any fails and you push the code , the git checks won't pass and pull requests won't merge**
    - minimum code coverage shall be 70%
