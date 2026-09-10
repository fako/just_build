Activating environments
-----------------------

When trying to run any invoke or Django command inside "management" use the following to enable environments:

```
source activate.sh
```

It can only be run from the root of the project.
This will not only activate a Python environment, but also load relevant environment variables that might be user specific


Style guidelines
----------------

Apart from using flake8 the following guidelines also apply.

We're using type hints compatible with Python 3.12.

In this repo we use double quotes wherever possible, because that's easier with the English language in strings.

Our max line length is 120 characters, because our screens are somewhat wider than terminals from the 70's.
Having multiple windows fit on a single screen is still beneficial, so having a limit is good.
But adding a comment with "noqa: E501" can make code more readable especially with long error messages.

Prefer function and method definitions on a single line when they fit reasonably.
If a signature needs wrapping, prefer a compact two-line rewrite over one argument per line where possible.
Do not introduce a vertical signature with a single argument on each line unless there is no cleaner option.
Favor left-to-right readability of the signature over strict adherence to the line limit when the result is still clear.
