# Scope
One time pop-ups which show that a new feature has been added.
# Design
Should always sit on top of the app. Nothing in the app should know that it exists or care about it. It should be working as an extension.

These should have their own JSON file, a registry of sorts, where every one of those is described. 
It should always consist of a title, a description, a place at which it points to, and optionally, an action when you press the button, which can be hidden. 

Visually they should be consistent with the app style. Show a title and a description, an X button(which dismisses them) and a "show me" button which can be hidden.

They show only once. If you dismiss it, ignore it or act, it should always count as it being acknowledged and no longer shown.
You can only see one every 24 hours. And when there are multiple waiting, you are always shown the newest one you did not yet see. 