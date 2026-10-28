import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../api/client.dart';
import '../../models/tool_spec.dart';
import '../../state/providers.dart';
import '../widgets/common.dart';
import '../widgets/schema_form.dart';

/// Generic runner for any hub tool: form generated from its schema, safety confirmation, result viewer.
class ToolRunScreen extends ConsumerStatefulWidget {
  const ToolRunScreen({super.key, required this.name, this.initial = const {}});
  final String name;
  final Map<String, dynamic> initial;

  @override
  ConsumerState<ToolRunScreen> createState() => _ToolRunScreenState();
}

class _ToolRunScreenState extends ConsumerState<ToolRunScreen> {
  final _form = GlobalKey<SchemaFormState>();
  Map<String, dynamic> _args = {};
  bool _busy = false;
  ToolResult? _result;
  Object? _error;

  /// A call is "live" when the tool can act on the outside world and dry-run is not explicitly on.
  bool _isLive(ToolSpec t) => (t.destructive || _args['auto_apply'] == true) && _args['dry_run'] != true;

  Future<bool> _confirmLive(ToolSpec t) async {
    return await showDialog<bool>(
          context: context,
          builder: (c) => AlertDialog(
            icon: const Icon(Icons.warning_amber_rounded),
            title: const Text('Run for real?'),
            content: Text('${t.title} may submit applications or send emails from your account.\n\n'
                'The server still enforces your daily caps, suppression list and the profile safety switch.'),
            actions: [
              TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
              FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Run live')),
            ],
          ),
        ) ??
        false;
  }

  Future<void> _run(ToolSpec t) async {
    if (!(_form.currentState?.validate() ?? true)) return;
    if (_isLive(t) && !await _confirmLive(t)) return;
    setState(() {
      _busy = true;
      _error = null;
      _result = null;
    });
    try {
      final r = await requireClientFrom(ref).call(t.name, _args);
      setState(() => _result = r);
      if (r.runId != null) ref.invalidate(runsProvider);
      if (const {'autopilot_stop', 'login_platform', 'suppress_contact', 'add_hr_contact'}.contains(t.name)) {
        ref.invalidate(statusProvider);
      }
    } catch (e) {
      setState(() => _error = e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final tools = ref.watch(toolsProvider);
    return Scaffold(
      appBar: AppBar(title: Text(widget.name.replaceAll('_', ' '))),
      body: AsyncBody<List<ToolSpec>>(
        value: tools,
        onRetry: () => ref.invalidate(toolsProvider),
        data: (list) {
          final t = list.where((x) => x.name == widget.name).firstOrNull;
          if (t == null) return Center(child: Text('Unknown tool ${widget.name}'));
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Text(t.description, style: Theme.of(context).textTheme.bodyMedium),
              const SizedBox(height: 8),
              Wrap(spacing: 8, children: [
                Chip(label: Text(t.category), visualDensity: VisualDensity.compact),
                if (t.readOnly) const Chip(avatar: Icon(Icons.visibility, size: 16), label: Text('read-only')),
                if (t.destructive) const Chip(avatar: Icon(Icons.bolt, size: 16), label: Text('acts on your accounts')),
              ]),
              const SizedBox(height: 16),
              SchemaForm(
                key: _form,
                properties: t.properties,
                required: t.required,
                initial: widget.initial,
                onChanged: (v) => _args = v,
              ),
              const SizedBox(height: 8),
              FilledButton.icon(
                onPressed: _busy ? null : () => _run(t),
                icon: _busy
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.play_arrow),
                label: Text(_busy ? 'Running…' : 'Run'),
              ),
              const SizedBox(height: 16),
              if (_error != null)
                Card(
                  color: Theme.of(context).colorScheme.errorContainer,
                  child: Padding(padding: const EdgeInsets.all(12), child: Text('$_error')),
                ),
              if (_result != null) ..._resultWidgets(_result!),
            ],
          );
        },
      ),
    );
  }

  List<Widget> _resultWidgets(ToolResult r) {
    final res = r.result;
    final remote = res is Map ? res['remote_browser_url'] as String? : null;
    return [
      Row(children: [
        Icon(r.isError ? Icons.error_outline : Icons.check_circle_outline,
            color: r.isError ? Theme.of(context).colorScheme.error : Colors.green),
        const SizedBox(width: 8),
        Expanded(child: Text(r.isError ? (r.error ?? 'Tool reported an error') : 'Done')),
      ]),
      if (res is Map && res['note'] != null)
        Padding(padding: const EdgeInsets.symmetric(vertical: 8), child: Text('${res['note']}')),
      if (r.isError && '${r.error}'.contains('login'))
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: FilledButton.icon(
            onPressed: () => context.push('/tools/login_platform', extra: {
              if (_args['platform'] is String) 'platform': _args['platform'],
            }),
            icon: const Icon(Icons.link_rounded),
            label: const Text('Link the account now'),
          ),
        ),
      if (r.runId != null)
        FilledButton.tonalIcon(
          onPressed: () => context.push('/runs/${Uri.encodeComponent(r.runId!)}'),
          icon: const Icon(Icons.terminal),
          label: const Text('Follow live log'),
        ),
      if (remote != null)
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: OutlinedButton.icon(
            onPressed: () => launchUrl(Uri.parse(remote), mode: LaunchMode.externalApplication),
            icon: const Icon(Icons.open_in_browser),
            label: const Text('Open remote browser to log in'),
          ),
        ),
      const SizedBox(height: 8),
      JsonView(res),
    ];
  }
}

ApiClient requireClientFrom(WidgetRef ref) {
  final c = ref.read(clientProvider);
  if (c == null) throw ApiException('Server not configured - open Settings');
  return c;
}
