"""Built-in task modules, discovered through the ``rhiza_task.tasks`` entry-point group.

Importing a module is what registers its tasks -- the ``@task`` decorator runs at import
time. Built-ins and third-party task modules arrive through the same entry point, so a
consumer-supplied task is a first-class citizen rather than an override. That is the
replacement for ``-include local.mk``.
"""
