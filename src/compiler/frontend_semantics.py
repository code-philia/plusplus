"""Shared prompt guidance for requirement-defined accessible UI contracts."""

UI_SEMANTIC_CONTRACT = """
Requirement notation such as "Note deleted"(role=status) defines BOTH the accessible
name and semantic role of the rendered element, not just visible text or an internal UI ID.
Preserve exact spelling, case and dynamic placeholders in these names. A node's snake_case
name is an internal design name; it must not replace the requirement's user-facing name.
Capture required roles and accessible names in node spec/description and semantic bindings
such as role, accessible_name and accessible_description. These are ordinary bindings,
not new top-level IR fields. Literal names use literal values; dynamic names are resolved
from the current item/state during implementation. Do not put unevaluated {title} in markup.
Examples: a feedback node has role=status and accessible_name="Note deleted"; a section
has role=region, accessible_name="Notes", and a description that follows Grid view/List view;
a dialog has accessible_name="Note labels" and contains labeled checkboxes and Done.
When a parsed page is binding-only, preserve its tree and record the exact name/role contract
in ui_requirement_updates.description. Later state/interaction passes retain that contract.
Each required action must produce its described result: wire its child event handler through
the owning screen, state and API calls. A visible Change labels action without a functioning
handler and reachable Note labels dialog does not implement the requirement. Connect every
specified entry point, including both a note menu and an editor when both are mentioned.
"""

REACT_SEMANTIC_EXAMPLES = """
Implement requirement-defined roles and accessible names in the DOM so a user or a role/name
locator can find the actual interactive element. Component names, test IDs, title attributes
and nearby text alone do not establish this contract. Prefer native HTML semantics; add an
explicit role when the requirement differs (main is role=main, not role=region).
Names and descriptions are separate: aria-describedby provides a description, not a name.
status and region do not automatically acquire an accessible name from their child text.
Use aria-label or aria-labelledby with a real unique referenced ID; keep visible labels too.
Do not add aria-label to arbitrary generic spans: use the meaningful required role/element.

React examples (adapt variables and handlers to existing typed contracts):
1. Named notification, including dynamic Undo feedback:
   <div role="status" aria-label={message}>
     <span>{message}</span><button onClick={undo}>Undo</button>
   </div>
   message is exactly "Note deleted", "Note archived" or "Action undone" as appropriate.
   This supports getByRole('status', { name: 'Note deleted', exact: true }).
2. Named collection with a separate layout description:
   <section role="region" aria-label="Notes" aria-describedby={viewDescriptionId}>
     <span id={viewDescriptionId}>{layout === 'grid' ? 'Grid view' : 'List view'}</span>
     {notes.map(note => <article key={note.id} role="region" aria-label={note.title}>
       <h3>{note.title}</h3>
       <button aria-label={`More options ${note.title}`} onClick={() => openMenu(note.id)}>⋮</button>
     </article>)}
   </section>
   Use useId() or stable unique IDs for descriptions and label associations; do not repeat IDs.
3. Dialog, checkbox and input names:
   <div role="dialog" aria-modal="true" aria-label="Note labels">
     {labels.map(label => <label key={label.id}>
       <input type="checkbox" checked={selected.includes(label.id)}
         onChange={event => changeSelection(label.id, event.target.checked)} />{label.name}
     </label>)}
     <button onClick={saveLabels}>Done</button>
   </div>
   <label htmlFor={titleInputId}>Title</label><input id={titleInputId} />
   <input type="search" aria-label="Search" value={query} onChange={handleSearch} />
   A placeholder alone is not a textbox label. Render a dialog only in its open state;
   aria-modal alone does not implement focus management, keyboard access or background inertness.
4. Child-to-parent event composition:
   <NoteEditor onChangeLabels={() => openLabelChooser(editingNoteId)} />
   <NoteMenu onChangeLabels={() => openLabelChooser(menuNoteId)} />
   Wire the actual generated prop names, not these illustrative names. The parent renders
   the chooser and owns its state; Done persists existing-note labels through the supplied API.
   For a new note, retain label IDs in the draft, then assign them after creation returns its ID.

Expose required checked/pressed/expanded/current state using the matching native/ARIA property.
Before returning edits, trace each required role/name to rendered DOM and each event from the
child prop through its caller to state/API/rendered result. Preserve valid tests: a missing
accessible name or role is an implementation defect when the requirement specifies it.
"""
