import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/providers.dart';
import '../widgets/common.dart';
import '../widgets/log_view.dart';
import 'tool_run_screen.dart';

/// Human label for a run record's state, shared by the list and Home activity card.
bool runIsRunning(Map<String, dynamic> r) => r['running'] == true;

bool runIsFailed(Map<String, dynamic> r) {
  final s = '${r['state']}';
  return s == 'failed' || s == 'error' || (r['exit_code'] is num && r['exit_code'] != 0);
}

/// Background runs (apply, autopilot, HR discovery, logins, post scans). Refreshes itself while something runs.
class RunsScreen extends ConsumerStatefulWidget {
  const RunsScreen({super.key});

  @override
  ConsumerState<RunsScreen> createState() => _RunsScreenState();
}

enum _RunFilter { all, running, failed }

class _RunsScreenState extends ConsumerState<RunsScreen> {
  Timer? _timer;
  _RunFilter _filter = _RunFilter.all;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 5), (_) {
      final list = ref.read(runsProvider).value;
      if (mounted && list != null && list.any(runIsRunning)) ref.invalidate(runsProvider);
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final runs = ref.watch(runsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Runs & logs'), actions: [
        IconButton(icon: const Icon(Icons.refresh), onPressed: () => ref.invalidate(runsProvider)),
      ]),
      body: AsyncBody<List<Map<String, dynamic>>>(
        value: runs,
        onRetry: () => ref.invalidate(runsProvider),
        data: (all) {
          final list = switch (_filter) {
            _RunFilter.all => all,
            _RunFilter.running => all.where(runIsRunning).toList(),
            _RunFilter.failed => all.where(runIsFailed).toList(),
          };
          return Column(children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
              child: Wrap(spacing: 8, children: [
                for (final (f, label) in [
                  (_RunFilter.all, 'All (${all.length})'),
                  (_RunFilter.running, 'Running (${all.where(runIsRunning).length})'),
                  (_RunFilter.failed, 'Failed (${all.where(runIsFailed).length})'),
                ])
                  ChoiceChip(label: Text(label), selected: _filter == f, onSelected: (_) => setState(() => _filter = f)),
              ]),
            ),
            Expanded(
              child: list.isEmpty
                  ? const Center(child: Text('Nothing here. Start a run from Home or Tools.'))
                  : RefreshIndicator(
                      onRefresh: () => ref.refresh(runsProvider.future),
                      child: ListView.separated(
                        itemCount: list.length,
                        separatorBuilder: (_, _) => const Divider(height: 1),
                        itemBuilder: (c, i) {
                          final r = list[i];
                          final tail = ((r['log_tail'] as List?) ?? const []).map((e) => '$e').toList();
                          return ListTile(
                            leading: runIsRunning(r)
                                ? const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2.5))
                                : Icon(runIsFailed(r) ? Icons.error_outline : Icons.check_circle_outline,
                                    color: runIsFailed(r) ? Theme.of(context).colorScheme.error : Colors.green),
                            title: Text('${r['kind']}${r['dry_run'] == true ? '  (dry run)' : ''}'),
                            subtitle: Text(
                              tail.isEmpty ? '${r['started']}' : '${r['started']}\n${tail.last}',
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                            ),
                            isThreeLine: tail.isNotEmpty,
                            trailing: StatusChip('${r['state']}'),
                            onTap: () => context.push('/runs/${Uri.encodeComponent('${r['run_id']}')}'),
                          );
                        },
                      ),
                    ),
            ),
          ]);
        },
      ),
    );
  }
}

/// Full log of one run (up to the last 500 lines), polled every 3 s while it is running.
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
    super.dispose();
  }

  Future<void> _poll() async {
    try {
      final r = await requireClientFrom(ref).run(widget.runId, tail: 500);
      if (!mounted) return;
      final wasRunning = _r?['running'] == true;
      setState(() {
        _r = r;
        _error = null;
      });
      if (wasRunning && r['running'] != true) ref.invalidate(runsProvider);
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
              Expanded(child: LogView(lines: lines)),
            ]),
    );
  }
}
