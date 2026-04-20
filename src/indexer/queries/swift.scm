;; Imports
(import_declaration
  (identifier) @import.source)

;; Classes / structs / enums / actors / extensions — tree-sitter-swift uses
;; the same class_declaration node with a `declaration_kind` child to
;; distinguish them. We treat them all as "class" for graph purposes.
(class_declaration
  name: (type_identifier) @symbol.class.name) @symbol.class.node

;; Protocols (interfaces)
(protocol_declaration
  name: (type_identifier) @symbol.interface.name) @symbol.interface.node

;; Functions — disambiguated to method by post-pass when inside a class/struct/protocol
(function_declaration
  name: (simple_identifier) @symbol.function.name) @symbol.function.node

;; Calls
(call_expression
  (simple_identifier) @call.callee)
