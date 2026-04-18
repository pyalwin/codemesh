;; Use declarations — capture the leftmost (top-level) module identifier.
;; For `use foo;` the argument is an identifier directly.
(use_declaration
  argument: (identifier) @import.source)

;; For `use std::foo;` the argument is a scoped_identifier whose path is an identifier.
(use_declaration
  argument: (scoped_identifier
    path: (identifier) @import.source))

;; For `use std::foo::bar;` (and deeper) the outer scoped_identifier's path is itself
;; a scoped_identifier whose path is the leftmost identifier.
(use_declaration
  argument: (scoped_identifier
    path: (scoped_identifier
      path: (identifier) @import.source)))

;; For `use std::foo::bar::baz;` — another level.
(use_declaration
  argument: (scoped_identifier
    path: (scoped_identifier
      path: (scoped_identifier
        path: (identifier) @import.source))))

;; Structs
(struct_item
  name: (type_identifier) @symbol.struct.name) @symbol.struct.node

;; Enums
(enum_item
  name: (type_identifier) @symbol.enum.name) @symbol.enum.node

;; Traits (interfaces)
(trait_item
  name: (type_identifier) @symbol.interface.name) @symbol.interface.node

;; Impl blocks — treat as container for nested functions to become methods via scopeOf().
(impl_item
  type: (type_identifier) @symbol.class.name) @symbol.class.node

;; Functions (free or inside impl; the post-pass reclassifies to method when in a container)
(function_item
  name: (identifier) @symbol.function.name) @symbol.function.node

;; Calls
(call_expression
  function: (identifier) @call.callee)

(call_expression
  function: (field_expression
    field: (field_identifier) @call.callee))
