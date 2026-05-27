; Go tree-sitter node-type reference for GoParser.
; This file is documentation only — GoParser uses a recursive AST walk, not
; tree-sitter query execution.

; Top-level declarations emitted as entities:
;   function_declaration   → FUNCTION  (name: identifier)
;   method_declaration     → METHOD    (receiver: parameter_list, name: field_identifier)
;   type_spec (struct)     → CLASS     (name: type_identifier, type: struct_type)
;   type_spec (interface)  → INTERFACE (name: type_identifier, type: interface_type)

; Import edges:
;   import_declaration → import_spec (path: interpreted_string_literal)
;   import_declaration → import_spec_list → import_spec ...

; Call edges (inside function/method bodies):
;   call_expression → function: identifier           ; simple call  foo()
;   call_expression → function: selector_expression  ; method call  obj.Method()
;                        → field: field_identifier

; Exported symbols: name starts with an uppercase letter (Go convention).

; Receiver type extraction from method_declaration:
;   receiver: parameter_list → parameter_declaration → type: (pointer_type | type_identifier)
;   regex `\*?\s*([A-Za-z_][A-Za-z0-9_]*)\)` extracts the type name.
