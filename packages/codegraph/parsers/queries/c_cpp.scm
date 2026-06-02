; C/C++ tree-sitter query reference (documentation only — parser uses the
; Python tree-sitter API directly rather than the query engine).
;
; Functions (top-level)
(function_definition declarator: (function_declarator declarator: (identifier) @function.name)) @function
;
; C structs
(struct_specifier name: (type_identifier) @struct.name body: (field_declaration_list)) @struct
(type_definition type: (struct_specifier) declarator: (type_identifier) @typedef.name) @typedef
;
; C++ classes / structs
(class_specifier name: (type_identifier) @class.name) @class
;
; C++ methods inside class body
(field_declaration_list
  (function_definition
    declarator: (function_declarator declarator: (field_identifier) @method.name))) @method
;
; Includes
(preproc_include path: (string_literal) @include.path) @include_local
(preproc_include path: (system_lib_string) @include.path) @include_system
;
; Call edges
(call_expression function: (identifier) @call.name) @direct_call
(call_expression function: (field_expression field: (field_identifier) @call.name)) @method_call
(call_expression function: (qualified_identifier name: (identifier) @call.name)) @scoped_call
