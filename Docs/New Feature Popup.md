# Scope
One time pop-ups which show that a new feature has been added.
# Design
Should always sit on top of the app. Nothing in the app should know that it exists or care about it. It should be working as an extension.

These should have their own registry, where every one of those is described:
- Title
- Description (show to the user)
- Place
- Expiry (default 1 month) (from 2 weeks to indefinitely)

Visually they should be consistent with the app style. Show a title and a description, an X button(which dismisses them)
If the target place is not visible on "home screen" a "show me" button appears which opens the place in the app.

They show only once. If you dismiss it, ignore it or act, it should always count as it being acknowledged and no longer shown.
- Ignore is counted when the user uses any of the major funcionalities of the app, such as search or timetable. 
  - The popup still stays until the reload
They are newer shown after they expire, they expiry timer starts when the feature is deployed.
You can only see one every 24 hours. And when there are multiple waiting, you are always shown the newest one you did not yet see.

[New Users] Do not see this
# Ideas
- a features registry, where you can scroll through all popups