import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/providers.dart';
import '../widgets/common.dart';
import 'tool_run_screen.dart';

/// Background runs (apply, autopilot, HR discovery, logins, post scans).
class RunsScreen extends ConsumerWidget {
  const RunsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final runs = ref.watch(runsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Runs'), actions: [
        IconButton(icon: const Icon(Icons.refresh), onPressed: () => ref.invalidate(runsProvider)),
      ]),
      body: AsyncBody<List<Map<String, dynamic>>>(
        value: runs,
        onRetry: () => ref.invalidate(runsProvider),
        data: (list) => list.isEmpty
            ? const Center(child: Text('No runs yet. Start one from Home or Tools.'))
            : RefreshIndicator(
                onRefresh: () => ref.refresh(runsProvider.future),
                child: ListView.separated(
                  itemCount: list.length,
                  separatorBuilder: (_, _) => const Divider(height: 1),
                  itemBuilder: (c, i) {
                    final r = list[i];
                    final tail = ((r['log_tail'] as List?) ?? const []).map((e) => '$e').toList();
                    return ListTile(
                      leading: Icon(r['running'] == true ? Icons.autorenew : Icons.terminal),
                      title: Text('${r['kind']}${r['dry_run'] == true ? '  (dry run)' : ''}'),
                      subtitle: Text(tail.isEmpty ? '${r['started']}' : tail.last, maxLines: 1, overflow: TextOverflow.ellipsis),
                      trailing: StatusChip('${r['state']}'),
                      onTap: () => context.push('/runs/${Uri.encodeComponent('${r['run_id']}')}'),
                    );
                  },
                ),
              ),
      ),
    );
  }
}

/// Live log of one run, polled every 3 s while it is running.
class RunDetailScreen extends ConsumerStatefulWidget {
  const RunDetailScreen({super.key, required this.runId});
  final String runId;

  @override
  ConsumerState<RunDetailScreen> createState() => _RunDetailScreenState();
}

class _RunDetailScreenState extends ConsumerState<RunDetailScreen> {
  Map<String, dynamic>? _r;
  Object? _error;
  Timer? _timer;
  final _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    _poll();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) {
      if (_r == null || _r!['running'] == true) _poll();
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _poll() async {
    try {
      final r = await requireClientFrom(ref).run(widget.runId, tail: 300);
      if (!mounted) return;
      final wasRunning = _r?['running'] == true;
      setState(() {
        _r = r;
        _error = null;
      });
      if (wasRunning && r['running'] != true) ref.invalidate(runsProvider);
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
      });
    } catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  Future<void> _cancel() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Stop this run?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Keep running')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Stop')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      final res = await requireClientFrom(ref).call('cancel_run', {'run_id': widget.runId});
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(res.error ?? 'Stop requested')));
      }
      _poll();
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = _r;
    final lines = ((r?['log_tail'] as List?) ?? const []).map((e) => '$e').toList();
    return Scaffold(
      appBar: AppBar(
        title: Text(r == null ? 'Run' : '${r['kind']}'),
        actions: [
          if (r?['running'] == true) IconButton(tooltip: 'Stop', icon: const Icon(Icons.stop_circle_outlined), onPressed: _cancel),
          IconButton(tooltip: 'Refresh', icon: const Icon(Icons.refresh), onPressed: _poll),
        ],
      ),
      body: r == null
          ? (_error == null ? const Center(child: CircularProgressIndicator()) : ErrorPanel(error: _error!, onRetry: _poll))
          : Column(children: [
              if (r['running'] == true) const LinearProgressIndicator(),
              ListTile(
                title: Text(widget.runId, style: const TextStyle(fontSize: 13)),
                subtitle: Text('started ${r['started']} · exit ${r['exit_code'] ?? '-'}'
                    '${r['dry_run'] == true ? ' · dry run' : ''}'),
                trailing: StatusChip('${r['state']}'),
              ),
              const Divider(height: 1),
              Expanded(
                child: Container(
                  color: Colors.black,
                  width: double.infinity,
                  child: ListView.builder(
                    controller: _scroll,
                    padding: const EdgeInsets.all(10),
                    itemCount: lines.length,
                    itemBuilder: (_, i) => SelectableText(lines[i],
                        style: const TextStyle(color: Color(0xFFB9F6CA), fontFamily: 'monospace', fontSize: 12)),
                  ),
                ),
              ),
            ]),
    );
  }
}
