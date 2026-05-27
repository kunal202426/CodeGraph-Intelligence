; Rust tree-sitter node-type reference for RustParser.
; This file is documentation only — RustParser uses a recursive AST walk, not
; tree-sitter query execution.

; Top-level declarations emitted as entities:
;   function_item          → FUNCTION  (name: identifier)
;   struct_item            → CLASS     (name: type_identifier)
;   enum_item              → CLASS     (name: type_identifier)
;   trait_item             → INTERFACE (name: type_identifier)
;   impl_item → function_item → METHOD (type field gives receiver type name)

; Visibility: presence of visibility_modifier child ("pub" / "pub(crate)" etc.)
;   → is_exported = true when any visibility_modifier child present

; Import edges:
;   use_declaration → argument: scoped_identifier | scoped_use_list | use_as_clause
;                              | use_wildcard | identifier
;   scoped_use_list: path field + list field (use_list with named item children)

; Call edges (inside function/method bodies):
;   call_expression → function: identifier               ; simple call  foo()
;   call_expression → function: field_expression         ; method call  self.method()
;                        → field: field_identifier
;   call_expression → function: scoped_identifier        ; path call    Type::new()
;                        → name: identifier (last segment)
;   macro_invocation nodes are skipped (not call_expression).

; Async detection: presence of "async" keyword child in function_item.
