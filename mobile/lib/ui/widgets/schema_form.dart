import 'package:flutter/material.dart';

/// Renders an input form from a tool's JSON-schema properties and collects typed arguments.
///
/// Supports string, integer, number, boolean, string enums and arrays of strings / enums - everything the hub
/// tool catalogue uses. Values left empty are omitted so the server applies its own (safe) defaults.
class SchemaForm extends StatefulWidget {
  const SchemaForm({
    super.key,
    required this.properties,
    required this.required,
    required this.onChanged,
    this.initial = const {},
  });

  final Map<String, Map<String, dynamic>> properties;
  final List<String> required;
  final Map<String, dynamic> initial;
  final ValueChanged<Map<String, dynamic>> onChanged;

  @override
  State<SchemaForm> createState() => SchemaFormState();
}

class SchemaFormState extends State<SchemaForm> {
  final _formKey = GlobalKey<FormState>();
  final Map<String, dynamic> _values = {};
  final Map<String, TextEditingController> _text = {};

  @override
  void initState() {
    super.initState();
    widget.properties.forEach((name, p) {
      final init = widget.initial[name] ?? p['default'];
      if (init != null) _values[name] = init;
      final t = p['type'];
      if (t == 'string' && p['enum'] == null || t == 'integer' || t == 'number' || _isFreeArray(p)) {
        final txt = init == null ? '' : (init is List ? init.join(', ') : '$init');
        _text[name] = TextEditingController(text: txt);
      }
    });
    WidgetsBinding.instance.addPostFrameCallback((_) => widget.onChanged(values));
  }

  @override
  void dispose() {
    for (final c in _text.values) {
      c.dispose();
    }
    super.dispose();
  }

  static bool _isFreeArray(Map<String, dynamic> p) =>
      p['type'] == 'array' && ((p['items'] as Map?)?['enum'] == null);

  /// Current arguments, without empty values.
  Map<String, dynamic> get values => Map.fromEntries(_values.entries.where((e) =>
      e.value != null && !(e.value is String && (e.value as String).isEmpty) && !(e.value is List && (e.value as List).isEmpty)));

  bool validate() => _formKey.currentState?.validate() ?? false;

  void _set(String name, dynamic v) {
    setState(() => _values[name] = v);
    widget.onChanged(values);
  }

  String _label(String name) => name.replaceAll('_', ' ') + (widget.required.contains(name) ? ' *' : '');

  Widget _field(String name, Map<String, dynamic> p) {
    final type = p['type'];
    final help = p['description'] as String?;
    final req = widget.required.contains(name);
    final enumVals = (p['enum'] as List?)?.map((e) => '$e').toList();

    if (type == 'boolean') {
      return SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: Text(_label(name)),
        subtitle: help == null ? null : Text(help),
        value: _values[name] == true,
        onChanged: (v) => _set(name, v),
      );
    }

    if (enumVals != null && (type == 'string' || type == 'integer')) {
      return DropdownButtonFormField<String>(
        initialValue: _values[name]?.toString(),
        decoration: InputDecoration(labelText: _label(name), helperText: help, helperMaxLines: 3),
        items: [
          if (!req) const DropdownMenuItem(value: null, child: Text('— default —')),
          ...enumVals.map((e) => DropdownMenuItem(value: e, child: Text(e))),
        ],
        validator: (v) => req && v == null ? 'required' : null,
        onChanged: (v) => _set(name, v == null ? null : (type == 'integer' ? int.parse(v) : v)),
      );
    }

    if (type == 'array' && !_isFreeArray(p)) {
      final options = ((p['items'] as Map)['enum'] as List).map((e) => '$e').toList();
      final selected = ((_values[name] as List?) ?? const []).map((e) => '$e').toSet();
      return InputDecorator(
        decoration: InputDecoration(labelText: _label(name), helperText: help, border: InputBorder.none),
        child: Wrap(
          spacing: 8,
          children: options
              .map((o) => FilterChip(
                    label: Text(o),
                    selected: selected.contains(o),
                    onSelected: (on) {
                      final next = {...selected};
                      on ? next.add(o) : next.remove(o);
                      _set(name, next.toList());
                    },
                  ))
              .toList(),
        ),
      );
    }

    final isNum = type == 'integer' || type == 'number';
    final isArray = type == 'array';
    final long = !isNum && !isArray && RegExp(r'jd_text|body|description').hasMatch(name);
    return TextFormField(
      controller: _text[name],
      keyboardType: isNum ? TextInputType.numberWithOptions(decimal: type == 'number') : (long ? TextInputType.multiline : null),
      minLines: long ? 4 : 1,
      maxLines: long ? 12 : 1,
      decoration: InputDecoration(
        labelText: _label(name),
        helperText: isArray ? '${help ?? ''}${help == null ? '' : ' · '}comma separated' : help,
        helperMaxLines: 3,
        hintText: [if (p['minimum'] != null) 'min ${p['minimum']}', if (p['maximum'] != null) 'max ${p['maximum']}'].join(', '),
      ),
      validator: (v) {
        final s = (v ?? '').trim();
        if (s.isEmpty) return req ? 'required' : null;
        if (isNum) {
          final n = num.tryParse(s);
          if (n == null || (type == 'integer' && n is! int)) return type == 'integer' ? 'whole number' : 'number';
          if (p['minimum'] is num && n < p['minimum']) return 'must be ≥ ${p['minimum']}';
          if (p['maximum'] is num && n > p['maximum']) return 'must be ≤ ${p['maximum']}';
        }
        return null;
      },
      onChanged: (v) {
        final s = v.trim();
        if (isArray) {
          _set(name, s.split(',').map((e) => e.trim()).where((e) => e.isNotEmpty).toList());
        } else if (isNum) {
          _set(name, s.isEmpty ? null : num.tryParse(s));
        } else {
          _set(name, s);
        }
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final names = widget.properties.keys.toList()
      ..sort((a, b) => (widget.required.contains(b) ? 1 : 0) - (widget.required.contains(a) ? 1 : 0));
    if (names.isEmpty) {
      return const Padding(padding: EdgeInsets.symmetric(vertical: 8), child: Text('This tool takes no inputs.'));
    }
    return Form(
      key: _formKey,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final n in names) Padding(padding: const EdgeInsets.only(bottom: 14), child: _field(n, widget.properties[n]!)),
        ],
      ),
    );
  }
}
