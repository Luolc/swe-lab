"""Oracle-guided trace synthesis — the code behind ``docs/trace-synthesis/``.

Deliberately re-exports nothing: ``oracle`` (the task) imports the workflow and
harness layers, and a package init that pulled it in would run those imports
for anyone who needed only one small module. Import the module you need.
"""
