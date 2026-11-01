import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../api/client.dart';
import '../../state/providers.dart';
import '../theme.dart';
import '../widgets/common.dart';
import '../widgets/queue_banner.dart';
import 'tool_run_screen.dart' show requireClientFrom;

const _days = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const _dayLabel = {'mon': 'Mon', 'tue': 'Tue', 'wed': 'Wed', 'thu': 'Thu', 'fri': 'Fri', 'sat': 'Sat', 'sun': 'Sun'};

/// Alarm-style schedule: at a clock time the server runs a list of tasks by itself (find HR emails, send emails, bulk
/// email, LinkedIn apply, Naukri apply ...). Tasks that start together wait in the run queue, six at a time.
class ScheduleScreen extends ConsumerWidget {
  const ScheduleScreen({super.key});

  Future<void> _runNow(BuildContext context, WidgetRef ref, Map<String, dynamic> args, String what) async {
    final preview = args['dry_run'] == true;
    final ok = preview ||
        await showDialog<bool>(
              context: context,
              builder: (c) => AlertDialog(
                icon: const Icon(Icons.warning_amber_rounded),
                title: const Text('Run for real?'),
                content: Text('$what will email contacts and apply on your accounts now, within your daily caps.'),
                actions: [
                  TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
                  FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Run live')),
                ],
              ),
            ) ==
            true;
    if (!ok || !context.mounted) return;
    try {
      final r = await requireClientFrom(ref).call('alarm_run_now', args);
      if (!context.mounted) return;
      ref.invalidate(queueProvider);
      ref.invalidate(runsProvider);
      final started = ((r.result as Map?)?['started'] as List?) ?? const [];
      final queued = started.where((e) => (e as Map)['queued'] == true).length;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(r.isError
              ? (r.error ?? 'failed')
              : 'Started ${started.length} task(s)${queued > 0 ? ', $queued waiting in the queue' : ''}')));
    } catch (e) {
      if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final alarms = ref.watch(alarmsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Schedule')),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _edit(context, ref, null, (alarms.value?['tasks'] as Map?)?.cast<String, dynamic>() ?? const {}),
        icon: const Icon(Icons.alarm_add_rounded),
        label: const Text('New alarm'),
      ),
      body: AsyncBody<Map<String, dynamic>>(
        value: alarms,
        onRetry: () => ref.invalidate(alarmsProvider),
        data: (d) {
          final list = ((d['alarms'] as List?) ?? const []).cast<Map<String, dynamic>>();
          final tasks = ((d['tasks'] as Map?) ?? const {}).cast<String, dynamic>();
          return RefreshIndicator(
            onRefresh: () => ref.refresh(alarmsProvider.future),
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 96),
              children: [
                const QueueBanner(),
                // 6-Slot High Concurrency Engine Banner
                Container(
                  margin: const EdgeInsets.only(bottom: 12),
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      colors: [
                        AppTheme.card,
                        AppTheme.cardElevated,
                      ],
                      begin: AlignmentDirectional.topStart,
                      end: AlignmentDirectional.bottomEnd,
                    ),
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: AppTheme.primary.withValues(alpha: 0.3)),
                  ),
                  child: Row(
                    children: [
                      Container(
                        padding: const EdgeInsets.all(8),
                        decoration: BoxDecoration(
                          color: AppTheme.primary.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: const Icon(Icons.bolt_rounded, color: AppTheme.secondaryLight, size: 22),
                      ),
                      const SizedBox(width: 12),
                      const Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Text('Parallel Engine',
                                    style: TextStyle(fontWeight: FontWeight.w700, fontSize: 14, color: AppTheme.textPrimary)),
                                SizedBox(width: 8),
                                StatusChip('6 Slots Max', isMini: true),
                              ],
                            ),
                            SizedBox(height: 4),
                            Text('Alarms dispatch tasks simultaneously in parallel without blocking each other.',
                                style: TextStyle(fontSize: 12, color: AppTheme.textSecondary)),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                Text('Server time ${'${d['server_time']}'.replaceFirst('T', ' ')}',
                    style: const TextStyle(color: AppTheme.textMuted, fontSize: 12)),
                const SizedBox(height: 8),
                Card(
                  child: ListTile(
                    leading: Container(
                      padding: const EdgeInsets.all(8),
                      decoration: BoxDecoration(
                        color: AppTheme.primary.withValues(alpha: 0.15),
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: const Icon(Icons.play_circle_fill_rounded, color: AppTheme.primaryLight, size: 22),
                    ),
                    title: const Text('Run all daily tasks now', style: TextStyle(fontWeight: FontWeight.w700)),
                    subtitle: const Text('Spawns up to 6 concurrent tasks: HR search, email sending, LinkedIn, Naukri'),
                    trailing: PopupMenuButton<bool>(
                      icon: const Icon(Icons.more_vert),
                      onSelected: (preview) =>
                          _runNow(context, ref, {'tasks': ['all'], 'dry_run': preview}, 'Running every daily task'),
                      itemBuilder: (_) => const [
                        PopupMenuItem(value: true, child: Text('Preview (dry run)')),
                        PopupMenuItem(value: false, child: Text('Run live')),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 12),
                if (list.isEmpty)
                  const Padding(
                    padding: EdgeInsets.all(24),
                    child: Text(
                      'No alarms yet. Tap New alarm, pick a time and the tasks, and the server runs them every day by itself.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: AppTheme.textSecondary),
                    ),
                  ),
                for (final al in list) _AlarmCard(al: al, tasks: tasks, onEdit: () => _edit(context, ref, al, tasks), onRun: _runNow),
              ],
            ),
          );
        },
      ),
    );
  }

  Future<void> _edit(BuildContext context, WidgetRef ref, Map<String, dynamic>? al, Map<String, dynamic> tasks) async {
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => _AlarmEditor(al: al, tasks: tasks),
    );
    if (saved == true) ref.invalidate(alarmsProvider);
  }
}

class _AlarmCard extends ConsumerWidget {
  const _AlarmCard({required this.al, required this.tasks, required this.onEdit, required this.onRun});
  final Map<String, dynamic> al;
  final Map<String, dynamic> tasks;
  final VoidCallback onEdit;
  final Future<void> Function(BuildContext, WidgetRef, Map<String, dynamic>, String) onRun;

  Widget _buildTaskBadge(String task, String label) {
    final (icon, color) = switch (task) {
      'send_emails' => (Icons.mark_email_read_rounded, AppTheme.accentIndigo),
      'find_emails' => (Icons.person_search_rounded, AppTheme.accentCyan),
      'bulk_email' => (Icons.forward_to_inbox_rounded, AppTheme.accentPurple),
      'linkedin_apply' => (Icons.business_center_rounded, AppTheme.accentSky),
      'naukri_apply' => (Icons.work_history_rounded, AppTheme.warning),
      'scan_posts' => (Icons.feed_rounded, AppTheme.accentTeal),
      'global_search' => (Icons.travel_explore_rounded, AppTheme.success),
      'whatsapp_send' => (Icons.chat_bubble_rounded, AppTheme.accentGreen),
      _ => (Icons.task_alt_rounded, AppTheme.primaryLight),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withValues(alpha: 0.3)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14, color: color),
          const SizedBox(width: 4),
          Text(label, style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final enabled = al['enabled'] == true;
    final days = ((al['days'] as List?) ?? const []).cast<String>();
    final last = (al['last'] as Map?)?.cast<String, dynamic>();
    final lastRuns = ((last?['runs'] as List?) ?? const []).cast<Map<String, dynamic>>();
    final failed = lastRuns.where((r) => r['error'] != null).length;
    final dry = al['dry_run'];
    final taskList = ((al['tasks'] as List?) ?? const []).cast<String>();

    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: AppTheme.card,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: enabled ? AppTheme.primary.withValues(alpha: 0.3) : AppTheme.borderSubtle,
        ),
      ),
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                '${al['time']}',
                style: TextStyle(
                  fontSize: 34,
                  fontWeight: FontWeight.w800,
                  color: enabled ? AppTheme.textPrimary : AppTheme.textMuted,
                  letterSpacing: -0.5,
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      days.isEmpty ? 'Every day' : days.map((d) => _dayLabel[d] ?? d).join(' '),
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                        color: enabled ? AppTheme.textPrimary : AppTheme.textMuted,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      dry == true ? '🛡️ Dry-run preview' : (dry == false ? '⚡ Live actions' : '⚙️ Profile safety'),
                      style: const TextStyle(fontSize: 11.5, color: AppTheme.textSecondary),
                    ),
                  ],
                ),
              ),
              Switch(
                value: enabled,
                onChanged: (v) async {
                  try {
                    await requireClientFrom(ref).call('alarm_set', {'id': al['id'], 'enabled': v});
                  } finally {
                    ref.invalidate(alarmsProvider);
                  }
                },
              ),
            ],
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final t in taskList)
                _buildTaskBadge(t, '${tasks[t] ?? t}'),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            [
              if (enabled && al['next'] != null) 'Next: ${'${al['next']}'.replaceFirst('T', ' ')}',
              if (!enabled) 'Alarm disabled',
              if (last != null) 'Last run: ${'${last['at']}'.replaceFirst('T', ' ')}${failed > 0 ? ' ($failed failed)' : ''}',
            ].join('  ·  '),
            style: const TextStyle(color: AppTheme.textMuted, fontSize: 11.5),
          ),
          const SizedBox(height: 8),
          const Divider(height: 16),
          Row(
            children: [
              FilledButton.tonalIcon(
                style: FilledButton.styleFrom(
                  visualDensity: VisualDensity.compact,
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                ),
                onPressed: () => onRun(context, ref, {'id': al['id'], 'dry_run': dry == true}, '“${al['label']}”'),
                icon: const Icon(Icons.play_arrow_rounded, size: 18),
                label: const Text('Run now'),
              ),
              const SizedBox(width: 8),
              OutlinedButton.icon(
                style: OutlinedButton.styleFrom(
                  visualDensity: VisualDensity.compact,
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                ),
                onPressed: onEdit,
                icon: const Icon(Icons.edit_outlined, size: 16),
                label: const Text('Edit'),
              ),
              const Spacer(),
              IconButton(
                tooltip: 'Delete alarm',
                icon: const Icon(Icons.delete_outline_rounded, size: 20),
                onPressed: () async {
                  final ok = await showDialog<bool>(
                    context: context,
                    builder: (c) => AlertDialog(
                      title: const Text('Delete this alarm?'),
                      actions: [
                        TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
                        FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Delete')),
                      ],
                    ),
                  );
                  if (ok == true) {
                    await requireClientFrom(ref).call('alarm_delete', {'id': al['id']});
                    ref.invalidate(alarmsProvider);
                  }
                },
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _AlarmEditor extends ConsumerStatefulWidget {
  const _AlarmEditor({required this.al, required this.tasks});
  final Map<String, dynamic>? al;
  final Map<String, dynamic> tasks;

  @override
  ConsumerState<_AlarmEditor> createState() => _AlarmEditorState();
}

class _AlarmEditorState extends ConsumerState<_AlarmEditor> {
  late TimeOfDay _time;
  late Set<String> _daySet;
  late Set<String> _taskSet;
  late bool _preview;
  late bool _autoApply;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    final a = widget.al;
    final parts = ('${a?['time'] ?? '09:00'}').split(':');
    _time = TimeOfDay(hour: int.tryParse(parts[0]) ?? 9, minute: int.tryParse(parts.length > 1 ? parts[1] : '0') ?? 0);
    _daySet = ((a?['days'] as List?) ?? const []).cast<String>().toSet();
    _taskSet = ((a?['tasks'] as List?) ?? const ['find_emails', 'send_emails', 'bulk_email']).cast<String>().toSet();
    _preview = a == null ? true : a['dry_run'] == true;
    _autoApply = a?['auto_apply'] == true;
  }

  String get _hhmm => '${_time.hour.toString().padLeft(2, '0')}:${_time.minute.toString().padLeft(2, '0')}';

  Future<void> _save() async {
    if (_taskSet.isEmpty) {
      setState(() => _error = 'Pick at least one task.');
      return;
    }
    if (widget.al == null && !_preview) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Run live every time?'),
          content: const Text('This alarm will send real emails and apply on your accounts each time it fires, within your daily caps. '
              'You can switch to preview only.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Back')),
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Keep live')),
          ],
        ),
      );
      if (ok != true) return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final r = await requireClientFrom(ref).call('alarm_set', {
        if (widget.al != null) 'id': widget.al!['id'],
        'time': _hhmm,
        'days': _daySet.toList(),
        'tasks': _taskSet.toList(),
        'auto_apply': _autoApply,
        'dry_run': _preview ? true : null,
      });
      if (r.isError) throw ApiException(r.error ?? 'could not save');
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = '$e';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(widget.al == null ? 'New alarm' : 'Edit alarm', style: Theme.of(context).textTheme.titleLarge),
            const SizedBox(height: 12),
            InkWell(
              borderRadius: BorderRadius.circular(14),
              onTap: () async {
                final t = await showTimePicker(context: context, initialTime: _time);
                if (t != null) setState(() => _time = t);
              },
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                decoration: BoxDecoration(color: AppTheme.card, borderRadius: BorderRadius.circular(14)),
                child: Row(children: [
                  const Icon(Icons.access_time_rounded),
                  const SizedBox(width: 12),
                  Text(_hhmm, style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w800)),
                  const Spacer(),
                  const Text('Tap to change', style: TextStyle(color: AppTheme.textMuted, fontSize: 12)),
                ]),
              ),
            ),
            const SizedBox(height: 14),
            const Text('Repeat', style: TextStyle(fontWeight: FontWeight.w700)),
            Wrap(spacing: 6, children: [
              for (final d in _days)
                FilterChip(
                  label: Text(_dayLabel[d]!),
                  selected: _daySet.contains(d),
                  onSelected: (v) => setState(() => v ? _daySet.add(d) : _daySet.remove(d)),
                ),
            ]),
            const Text('No day selected = every day', style: TextStyle(color: AppTheme.textMuted, fontSize: 12)),
            const SizedBox(height: 14),
            Row(children: [
              const Text('Tasks', style: TextStyle(fontWeight: FontWeight.w700)),
              const Spacer(),
              TextButton(
                onPressed: () => setState(() => _taskSet = {'find_emails', 'send_emails', 'bulk_email', 'linkedin_apply', 'naukri_apply'}),
                child: const Text('All daily tasks'),
              ),
            ]),
            Wrap(spacing: 6, runSpacing: 4, children: [
              for (final e in widget.tasks.entries)
                FilterChip(
                  label: Text('${e.value}'),
                  selected: _taskSet.contains(e.key),
                  onSelected: (v) => setState(() => v ? _taskSet.add(e.key) : _taskSet.remove(e.key)),
                ),
            ]),
            const SizedBox(height: 8),
            if (_taskSet.contains('scan_posts') || _taskSet.contains('global_search'))
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Apply to what the scan / search finds'),
                value: _autoApply,
                onChanged: (v) => setState(() => _autoApply = v),
              ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Preview only (dry run)'),
              subtitle: Text(_preview
                  ? 'Nothing is sent or submitted.'
                  : 'Off: uses your profile safety setting. If live actions are confirmed there, real emails go out.'),
              value: _preview,
              onChanged: (v) => setState(() => _preview = v),
            ),
            if (_error != null) Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            const SizedBox(height: 8),
            Row(children: [
              TextButton(onPressed: () => context.pop(), child: const Text('Cancel')),
              const Spacer(),
              FilledButton.icon(
                onPressed: _saving ? null : _save,
                icon: _saving
                    ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Icon(Icons.alarm_on_rounded),
                label: const Text('Save alarm'),
              ),
            ]),
          ],
        ),
      ),
    );
  }
}
