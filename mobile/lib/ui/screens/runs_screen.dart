import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/providers.dart';
import '../theme.dart';
import '../widgets/common.dart';
import '../widgets/log_view.dart';
import 'tool_run_screen.dart';

/// Human label for a run record's state, shared by the list and Home activity card.
bool runIsRunning(Map<String, dynamic> r) => r['running'] == true;

bool runIsFailed(Map<String, dynamic> r) {
  final s = '${r['state']}';
  return s == 'failed' || s == 'error' || (r['exit_code'] is num && r['exit_code'] != 0);
}

/// Modern Background Runs & Console Logs monitor.
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
      appBar: AppBar(
        title: const Text('Activity & Runs'),
        actions: [
          IconButton(
            tooltip: 'Refresh logs',
            icon: const Icon(Icons.refresh_rounded),
            onPressed: () => ref.invalidate(runsProvider),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: AsyncBody<List<Map<String, dynamic>>>(
        value: runs,
        onRetry: () => ref.invalidate(runsProvider),
        data: (all) {
          final runningCount = all.where(runIsRunning).length;
          final failedCount = all.where(runIsFailed).length;
          final okCount = all.length - runningCount - failedCount;

          final list = switch (_filter) {
            _RunFilter.all => all,
            _RunFilter.running => all.where(runIsRunning).toList(),
            _RunFilter.failed => all.where(runIsFailed).toList(),
          };

          return Column(
            children: [
              // Summary KPI Row
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
                child: Row(
                  children: [
                    _MiniStat('Total', '${all.length}', AppTheme.primaryLight),
                    const SizedBox(width: 8),
                    _MiniStat('Running', '$runningCount', AppTheme.secondaryLight),
                    const SizedBox(width: 8),
                    _MiniStat('Passed', '$okCount', AppTheme.successLight),
                    const SizedBox(width: 8),
                    _MiniStat('Failed', '$failedCount', AppTheme.error),
                  ],
                ),
              ),
              // Filter Chips
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                child: Row(
                  children: [
                    ChoiceChip(
                      label: Text('All (${all.length})'),
                      selected: _filter == _RunFilter.all,
                      onSelected: (_) => setState(() => _filter = _RunFilter.all),
                    ),
                    const SizedBox(width: 8),
                    ChoiceChip(
                      avatar: runningCount > 0
                          ? const SizedBox(
                              width: 12,
                              height: 12,
                              child: CircularProgressIndicator(strokeWidth: 1.5, color: AppTheme.secondaryLight),
                            )
                          : null,
                      label: Text('Running ($runningCount)'),
                      selected: _filter == _RunFilter.running,
                      onSelected: (_) => setState(() => _filter = _RunFilter._running),
                    ),
                    const SizedBox(width: 8),
                    ChoiceChip(
                      label: Text('Failed ($failedCount)'),
                      selected: _filter == _RunFilter.failed,
                      onSelected: (_) => setState(() => _filter = _RunFilter.failed),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 4),
              // Runs List
              Expanded(
                child: list.isEmpty
                    ? Center(
                        child: Padding(
                          padding: const EdgeInsets.all(24),
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Container(
                                padding: const EdgeInsets.all(16),
                                decoration: BoxDecoration(
                                  color: AppTheme.card,
                                  shape: BoxShape.circle,
                                  border: Border.all(color: AppTheme.borderSubtle),
                                ),
                                child: const Icon(Icons.history_rounded, size: 40, color: AppTheme.textMuted),
                              ),
                              const SizedBox(height: 16),
                              const Text('No runs recorded',
                                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: AppTheme.textPrimary)),
                              const SizedBox(height: 6),
                              const Text('Trigger an autopilot run or job board search from the Home screen.',
                                  textAlign: TextAlign.center,
                                  style: TextStyle(fontSize: 13, color: AppTheme.textSecondary)),
                            ],
                          ),
                        ),
                      )
                    : RefreshIndicator(
                        onRefresh: () => ref.refresh(runsProvider.future),
                        child: ListView.separated(
                          padding: const EdgeInsets.fromLTRB(16, 6, 16, 80),
                          itemCount: list.length,
                          separatorBuilder: (_, _) => const SizedBox(height: 10),
                          itemBuilder: (c, i) => _RunCard(list[i]),
                        ),
                      ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _MiniStat extends StatelessWidget {
  const _MiniStat(this.label, this.val, this.color);
  final String label;
  final String val;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 10),
        decoration: BoxDecoration(
          color: AppTheme.card,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: AppTheme.borderSubtle),
        ),
        child: Column(
          children: [
            Text(val, style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800, color: color)),
            const SizedBox(height: 2),
            Text(label, style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w500, color: AppTheme.textMuted)),
          ],
        ),
      ),
    );
  }
}

class _RunCard extends StatelessWidget {
  const _RunCard(this.r);
  final Map<String, dynamic> r;

  IconData _iconForKind(String kind) {
    if (kind.contains('autopilot')) return Icons.smart_toy_rounded;
    if (kind.contains('login')) return Icons.login_rounded;
    if (kind.contains('post')) return Icons.dynamic_feed_rounded;
    if (kind.contains('hr')) return Icons.people_outline_rounded;
    if (kind.contains('search')) return Icons.search_rounded;
    return Icons.terminal_rounded;
  }

  String _cleanSummary(List<dynamic> tail) {
    if (tail.isEmpty) return 'No log output';
    // If traceback, pick the last non-empty meaningful line
    for (int i = tail.length - 1; i >= 0; i--) {
      final line = '${tail[i]}'.trim();
      if (line.isNotEmpty && !line.startsWith('File ') && !line.startsWith('Traceback')) {
        return line;
      }
    }
    return '${tail.last}';
  }

  @override
  Widget build(BuildContext context) {
    final running = runIsRunning(r);
    final failed = runIsFailed(r);
    final kind = '${r['kind']}';
    final dryRun = r['dry_run'] == true;
    final tail = ((r['log_tail'] as List?) ?? const []).toList();
    final summary = _cleanSummary(tail);
    final icon = _iconForKind(kind);

    return InkWell(
      borderRadius: BorderRadius.circular(16),
      onTap: () => context.push('/runs/${Uri.encodeComponent('${r['run_id']}')}'),
      child: Container(
        decoration: BoxDecoration(
          color: AppTheme.card,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: failed
                ? AppTheme.error.withValues(alpha: 0.3)
                : running
                    ? AppTheme.primary.withValues(alpha: 0.4)
                    : AppTheme.borderSubtle,
          ),
        ),
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: failed
                        ? AppTheme.error.withValues(alpha: 0.12)
                        : running
                            ? AppTheme.primary.withValues(alpha: 0.12)
                            : AppTheme.surface,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: running
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2, color: AppTheme.primaryLight),
                        )
                      : Icon(
                          icon,
                          size: 18,
                          color: failed ? AppTheme.error : AppTheme.primaryLight,
                        ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Flexible(
                            child: Text(
                              kind,
                              style: const TextStyle(
                                fontSize: 14.5,
                                fontWeight: FontWeight.w700,
                                color: AppTheme.textPrimary,
                              ),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          if (dryRun) ...[
                            const SizedBox(width: 6),
                            const StatusChip('dry-run', isMini: true),
                          ],
                        ],
                      ),
                      const SizedBox(height: 2),
                      Text(
                        '${r['started']}',
                        style: const TextStyle(fontSize: 11.5, color: AppTheme.textMuted),
                      ),
                    ],
                  ),
                ),
                StatusChip('${r['state']}', isMini: true),
              ],
            ),
            const SizedBox(height: 10),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
              decoration: BoxDecoration(
                color: AppTheme.surface,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(
                  color: failed ? AppTheme.error.withValues(alpha: 0.2) : AppTheme.borderSubtle,
                ),
              ),
              child: Text(
                summary,
                style: TextStyle(
                  fontFamily: 'monospace',
                  fontSize: 11.5,
                  color: failed ? const Color(0xFFFCA5A5) : AppTheme.textSecondary,
                ),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Detailed run log viewer with clean header, status pill, auto-refresh, and terminal output.
class RunDetailScreen extends ConsumerStatefulWidget {
  const RunDetailScreen({super.key, required this.runId});
  final String runId;

  @override
  ConsumerState<RunDetailScreen> createState() => _RunDetailScreenState();
}

class _RunDetailScreenState extends ConsumerState<RunDetailScreen> {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) {
      final r = ref.read(runDetailProvider(widget.runId)).value;
      if (mounted && r != null && runIsRunning(r)) {
        ref.invalidate(runDetailProvider(widget.runId));
      }
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final detail = ref.watch(runDetailProvider(widget.runId));

    return Scaffold(
      appBar: AppBar(
        title: Text(widget.runId, style: const TextStyle(fontSize: 15, fontFamily: 'monospace')),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh_rounded),
            onPressed: () => ref.invalidate(runDetailProvider(widget.runId)),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: AsyncBody<Map<String, dynamic>>(
        value: detail,
        onRetry: () => ref.invalidate(runDetailProvider(widget.runId)),
        data: (r) {
          final running = runIsRunning(r);
          final tail = ((r['log_tail'] as List?) ?? const []).map((e) => '$e').toList();

          return Column(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                color: AppTheme.card,
                child: Row(
                  children: [
                    StatusChip('${r['state']}'),
                    const SizedBox(width: 10),
                    if (r['dry_run'] == true) const StatusChip('dry-run', isMini: true),
                    const Spacer(),
                    if (running)
                      FilledButton.tonalIcon(
                        style: FilledButton.styleFrom(
                          backgroundColor: AppTheme.error.withValues(alpha: 0.15),
                          foregroundColor: AppTheme.error,
                          visualDensity: VisualDensity.compact,
                        ),
                        onPressed: () async {
                          try {
                            await requireClientFrom(ref).call('cancel_run', {'run_id': widget.runId});
                            ref.invalidate(runDetailProvider(widget.runId));
                          } catch (e) {
                            if (context.mounted) {
                              ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                            }
                          }
                        },
                        icon: const Icon(Icons.cancel_rounded, size: 16),
                        label: const Text('Cancel Run'),
                      ),
                  ],
                ),
              ),
              const Divider(height: 1),
              Expanded(
                child: tail.isEmpty
                    ? const Center(child: Text('No logs recorded.', style: TextStyle(color: AppTheme.textMuted)))
                    : LogView(lines: tail),
              ),
            ],
          );
        },
      ),
    );
  }
}
