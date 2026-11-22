# Scope
One time pop-ups which show that a new feature has been added.
# Design
Nothing in the app knows that it exists or care about it. Imagine it working as an extension.

These have their own registry, where every one of those is described:
- Title
- Description (show to the user)
- Place (tab/panel/field where the feature sits)
- Expiry (default 1 month) (from 2 weeks to indefinitely)

Visually they are to be consistent with the app style. Show a title and a description, an X button(which dismisses them). They shouldn't care about what they are covering under, what matters is that they point properly and are readable. They also point at the place
- If the target place is not visible on "home screen" a "show me" button appears which opens the place in the app.
- If the feature appears only after an action, the place is where that action starts, and the description says what to do.

If you dismiss it, ignore it or act, it always counts as it being acknowledged and is no longer shown.
- Ignore is counted when the user uses any of the major functionalities of the app, such as search or timetable. 
  - The popup still stays until the reload
They are never shown after they expire, the expiry timer starts when the feature is deployed.
You can only see one every 24 hours. And when there are multiple waiting, you are always shown the newest one you did not yet see.

[New Users] Do not see this
# Ideas
- a features registry, where you can scroll through all popups