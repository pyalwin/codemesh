;; Package-level imports. `import_spec` can have path as interpreted_string_literal.
(import_spec
  path: (interpreted_string_literal) @import.source)

;; Structs (mapped to class by KIND_BY_CAPTURE)
(type_declaration
  (type_spec
    name: (type_identifier) @symbol.struct.name
    type: (struct_type))) @symbol.struct.node

;; Interfaces
(type_declaration
  (type_spec
    name: (type_identifier) @symbol.interface.name
    type: (interface_type))) @symbol.interface.node

;; Top-level functions
(function_declaration
  name: (identifier) @symbol.function.name) @symbol.function.node

;; Methods (receiver present)
(method_declaration
  name: (field_identifier) @symbol.method.name) @symbol.method.node

;; Calls
(call_expression
  function: (identifier) @call.callee)

(call_expression
  function: (selector_expression
    field: (field_identifier) @call.callee))
