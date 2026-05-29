; PHP tree-sitter query reference (documentation only — parser uses the
; Python tree-sitter API directly rather than the query engine).
;
; Top-level type declarations
(class_declaration name: (name) @class.name) @class
(interface_declaration name: (name) @interface.name) @interface
(trait_declaration name: (name) @trait.name) @trait
;
; Top-level function
(function_definition name: (name) @function.name) @function
;
; Methods inside class / interface / trait body
(method_declaration name: (name) @method.name) @method
;
; Imports
(namespace_use_declaration) @use
(require_once_expression) @require_once
(require_expression) @require
(include_expression) @include
(include_once_expression) @include_once
;
; Call edges
(member_call_expression name: (name) @call.name) @member_call
(function_call_expression function: (name) @call.name) @function_call
(scoped_call_expression name: (name) @call.name) @scoped_call
