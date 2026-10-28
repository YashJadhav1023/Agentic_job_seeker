import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../api/client.dart';
import '../../state/providers.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'tool_run_screen.dart' show requireClientFrom;

const _boards = <(String, String)>[
  ('linkedin', 'LinkedIn'),
  ('indeed', 'Indeed'),
  ('naukri', 'Naukri'),
  ('google', 'Google Jobs'),
  ('glassdoor', 'Glassdoor'),
];

enum _Phase { form, searching, results }

/// Global search and apply: searches every board for your roles, ranks the jobs against your resume, and applies to the
/// ones you pick (or to all good matches automatically).
class GlobalSearchScreen extends ConsumerStatefulWidget {
  const GlobalSearchScreen({super.key});

  @override
  ConsumerState<GlobalSearchScreen> createState() => _GlobalSearchScreenState();
}

class _GlobalSearchScreenState extends ConsumerState<GlobalSearchScreen> {
  final _query = TextEditingController();
  final _location = TextEditingController();
  final _sites = <String>{'linkedin', 'indeed', 'naukri', 'google'};
  bool _autoApply = false;
  bool _preview = true; // dry run: drafts and form fills only, nothing is sent
  _Phase _phase = _Phase.form;
  String? _runId;
  String? _error;
  Map<String, dynamic>? _run;
  List<Map<String, dynamic>> _jobs = [];
  final _picked = <int>{};
  Timer? _timer;
  bool _applying = false;
  String? _applyRun;

  @override
  void dispose() {
    _timer?.cancel();
    _query.dispose();
    _location.dispose();
    super.dispose();
  }

  Future<bool> _confirmLive(String what) async =>
      await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          icon: const Icon(Icons.warning_amber_rounded),
          title: const Text('Apply for real?'),
          content: Text('$what will send emails and submit application forms from your accounts.\n\n'
              'Your daily caps, send window and suppression list still apply.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Apply live')),
          ],
        ),
      ) ??
      false;

  Future<void> _search() async {
    if (_sites.isEmpty) {
      setState(() => _error = 'Pick at least one job board.');
      return;
    }
    if (_autoApply && !_preview && !await _confirmLive('Auto-apply')) return;
    setState(() {
      _error = null;
      _phase = _Phase.searching;
      _run = null;
      _picked.clear();
      _jobs = [];
    });
    try {
      final r = await requireClientFrom(ref).call('global_search', {
        if (_query.text.trim().isNotEmpty) 'query': _query.text.trim(),
        if (_location.text.trim().isNotEmpty) 'location': _location.text.trim(),
        'sites': _sites.toList(),
        'auto_apply': _autoApply,
        'dry_run': _preview,
      });
      if (r.isError || r.runId == null) throw ApiException(r.error ?? 'could not start the search');
      _runId = r.runId;
      ref.invalidate(runsProvider);
      ref.invalidate(queueProvider);
      _timer = Timer.periodic(const Duration(seconds: 4), (_) => _poll());
    } catch (e) {
      setState(() {
        _error = '$e';
        _phase = _Phase.form;
      });
    }
  }

  Future<void> _poll() async {
    final id = _runId;
    if (id == null) return;
    try {
      final st = await requireClientFrom(ref).run(id, tail: 12);
      if (!mounted) return;
      setState(() => _run = st);
      final queued = st['queued'] == true;
      if (!queued && st['running'] != true) {
        _timer?.cancel();
        if (st['state'] == 'ok') {
          await _loadResults(id);
        } else {
          setState(() {
            _error = 'The search run ${st['state']}. Open the run log for details.';
            _phase = _Phase.form;
          });
        }
      }
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  Future<void> _loadResults(String id) async {
    final r = await requireClientFrom(ref).call('search_results', {'run_id': id});
    if (!mounted) return;
    if (r.isError) {
      setState(() {
        _error = r.error;
        _phase = _Phase.form;
      });
      return;
    }
    final res = (r.result as Map).cast<String, dynamic>();
    final jobs = ((res['jobs'] as List?) ?? const []).cast<Map<String, dynamic>>();
    setState(() {
      _jobs = jobs;
      _phase = _Phase.results;
      _applyRun = (res['auto_apply'] as Map?)?['run_id'] as String?;
    });
  }

  bool _good(Map<String, dynamic> j) => const {'scored', 'queued'}.contains(j['status']) && j['id'] is int;

  Future<void> _apply(List<int> ids) async {
    if (ids.isEmpty) return;
    if (!_preview && !await _confirmLive('Applying to ${ids.length} job(s)')) return;
    setState(() => _applying = true);
    try {
      final r = await requireClientFrom(ref).call('apply_to_jobs', {'job_ids': ids, 'dry_run': _preview});
      if (!mounted) return;
      if (r.isError) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(r.error ?? 'could not start')));
      } else {
        setState(() {
          _applyRun = r.runId;
          _picked.clear();
        });
        ref.invalidate(runsProvider);
        ref.invalidate(queueProvider);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text(_preview ? 'Preview started for ${ids.length} job(s)' : 'Applying to ${ids.length} job(s)')));
      }
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _applying = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Search all boards'),
        actions: [
          if (_phase != _Phase.form)
            IconButton(
              tooltip: 'New search',
              icon: const Icon(Icons.restart_alt_rounded),
              onPressed: () {
                _timer?.cancel();
                setState(() => _phase = _Phase.form);
              },
            ),
        ],
      ),
      body: switch (_phase) {
        _Phase.form => _form(),
        _Phase.searching => _searching(),
        _Phase.results => _results(),
      },
    );
  }

  Widget _form() {
    final scheme = Theme.of(context).colorScheme;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const Text(
          'Finds jobs for your roles and cities on every board you tick, ranks them against your resume, and can apply to the good ones.',
          style: TextStyle(color: AppTheme.textSecondary, fontSize: 13),
        ),
        const SizedBox(height: 14),
        TextField(
          controller: _query,
          decoration: const InputDecoration(
            labelText: 'Job title (optional)',
            helperText: 'Empty = all roles in your profile',
            prefixIcon: Icon(Icons.work_outline_rounded),
          ),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _location,
          decoration: const InputDecoration(
            labelText: 'City or Remote (optional)',
            helperText: 'Empty = all cities in your profile',
            prefixIcon: Icon(Icons.place_outlined),
          ),
        ),
        const SizedBox(height: 14),
        const Text('Boards', style: TextStyle(fontWeight: FontWeight.w700)),
        const SizedBox(height: 6),
        Wrap(spacing: 8, runSpacing: 4, children: [
          for (final (id, label) in _boards)
            FilterChip(
              label: Text(label),
              selected: _sites.contains(id),
              onSelected: (v) => setState(() => v ? _sites.add(id) : _sites.remove(id)),
            ),
        ]),
        const SizedBox(height: 8),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Apply automatically to good matches'),
          subtitle: const Text('Company form, or a tailored email with your resume to the verified contact'),
          value: _autoApply,
          onChanged: (v) => setState(() => _autoApply = v),
        ),
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Preview only (dry run)'),
          subtitle: Text(_preview
              ? 'Nothing is sent or submitted. You see what would happen.'
              : 'LIVE: real emails and applications within your daily caps.'),
          value: _preview,
          onChanged: (v) => setState(() => _preview = v),
        ),
        if (_error != null)
          Card(
            color: scheme.errorContainer,
            child: Padding(padding: const EdgeInsets.all(12), child: Text(_error!)),
          ),
        const SizedBox(height: 8),
        FilledButton.icon(
          onPressed: _search,
          icon: const Icon(Icons.travel_explore_rounded),
          label: Text(_autoApply ? 'Search and apply' : 'Search'),
        ),
      ],
    );
  }

  Widget _searching() {
    final queued = _run?['queued'] == true;
    final tail = ((_run?['log_tail'] as List?) ?? const []).map((e) => '$e').toList();
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const SizedBox(height: 24),
        const Center(child: CircularProgressIndicator()),
        const SizedBox(height: 16),
        Center(
          child: Text(
            queued ? 'Waiting for a free slot (position ${_run?['position']})…' : 'Searching ${_sites.length} boards…',
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
        ),
        const SizedBox(height: 6),
        const Center(child: Text('This takes a minute or two.', style: TextStyle(color: AppTheme.textSecondary))),
        const SizedBox(height: 16),
        if (tail.isNotEmpty)
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(color: AppTheme.surface, borderRadius: BorderRadius.circular(12)),
            child: Text(tail.join('\n'),
                style: const TextStyle(fontFamily: 'monospace', fontSize: 11.5, color: AppTheme.textSecondary)),
          ),
        if (_runId != null)
          TextButton.icon(
            onPressed: () => context.push('/runs/${Uri.encodeComponent(_runId!)}'),
            icon: const Icon(Icons.terminal),
            label: const Text('Open full log'),
          ),
      ],
    );
  }

  Widget _results() {
    final good = _jobs.where(_good).toList();
    return Column(
      children: [
        Container(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 10),
          color: AppTheme.surface,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('${_jobs.length} jobs found · ${good.length} good matches',
                  style: const TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: 6),
              Row(children: [
                Expanded(
                  child: Text(
                    _preview ? 'Preview mode: nothing is sent.' : 'LIVE mode: real emails and applications.',
                    style: TextStyle(color: _preview ? AppTheme.textSecondary : AppTheme.warningLight, fontSize: 12.5),
                  ),
                ),
                Switch(value: _preview, onChanged: (v) => setState(() => _preview = v)),
                const Text('Preview', style: TextStyle(fontSize: 12)),
              ]),
              Wrap(spacing: 8, children: [
                OutlinedButton(
                  onPressed: good.isEmpty
                      ? null
                      : () => setState(() {
                            _picked
                              ..clear()
                              ..addAll(good.map((j) => j['id'] as int));
                          }),
                  child: Text('Select good (${good.length})'),
                ),
                FilledButton.icon(
                  onPressed: _picked.isEmpty || _applying ? null : () => _apply(_picked.toList()),
                  icon: _applying
                      ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.send_rounded, size: 18),
                  label: Text('Apply to ${_picked.length} selected'),
                ),
              ]),
              if (_applyRun != null)
                TextButton.icon(
                  onPressed: () => context.push('/runs/${Uri.encodeComponent(_applyRun!)}'),
                  icon: const Icon(Icons.terminal, size: 18),
                  label: const Text('Follow the apply run'),
                ),
            ],
          ),
        ),
        Expanded(
          child: _jobs.isEmpty
              ? const Center(child: Text('No jobs matched your title rules. Try other boards or a wider title.'))
              : ListView.builder(
                  padding: const EdgeInsets.all(12),
                  itemCount: _jobs.length,
                  itemBuilder: (_, i) => _JobTile(
                    job: _jobs[i],
                    picked: _picked.contains(_jobs[i]['id']),
                    selectable: _good(_jobs[i]),
                    onToggle: (v) => setState(() => v ? _picked.add(_jobs[i]['id'] as int) : _picked.remove(_jobs[i]['id'])),
                    onApply: () => _apply([_jobs[i]['id'] as int]),
                  ),
                ),
        ),
      ],
    );
  }
}

class _JobTile extends StatelessWidget {
  const _JobTile({required this.job, required this.picked, required this.selectable, required this.onToggle, required this.onApply});
  final Map<String, dynamic> job;
  final bool picked;
  final bool selectable;
  final ValueChanged<bool> onToggle;
  final VoidCallback onApply;

  @override
  Widget build(BuildContext context) {
    final score = (job['score'] is num) ? (job['score'] as num).round() : 0;
    final matched = ((job['matched'] as List?) ?? const []).take(5).join(', ');
    final status = '${job['status']}';
    final color = score >= 70 ? AppTheme.success : (score >= 45 ? AppTheme.warning : AppTheme.textMuted);
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              if (selectable) Checkbox(value: picked, onChanged: (v) => onToggle(v ?? false)),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('${job['title']}', style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14.5)),
                  const SizedBox(height: 2),
                  Text('${job['company'] ?? ''}  ·  ${job['location'] ?? ''}',
                      style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12.5)),
                ]),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(color: color.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(10)),
                child: Text('$score', style: TextStyle(color: color, fontWeight: FontWeight.w800)),
              ),
            ]),
            const SizedBox(height: 6),
            Wrap(spacing: 6, runSpacing: 4, children: [
              StatusChip('${job['source'] ?? ''}'.replaceAll('jobspy-', ''), isMini: true),
              StatusChip(status, isMini: true),
            ]),
            if (matched.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text('Matches: $matched', style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12)),
              ),
            if ('${job['reason'] ?? ''}'.isNotEmpty && !selectable)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('${job['reason']}', style: const TextStyle(color: AppTheme.textMuted, fontSize: 11.5)),
              ),
            const SizedBox(height: 4),
            Row(children: [
              TextButton.icon(
                onPressed: () => launchUrl(Uri.parse('${job['url']}'), mode: LaunchMode.externalApplication),
                icon: const Icon(Icons.open_in_new, size: 16),
                label: const Text('Open'),
              ),
              const Spacer(),
              if (selectable)
                FilledButton.tonalIcon(
                  onPressed: onApply,
                  icon: const Icon(Icons.bolt_rounded, size: 16),
                  label: const Text('Auto-apply'),
                ),
            ]),
          ],
        ),
      ),
    );
  }
}
