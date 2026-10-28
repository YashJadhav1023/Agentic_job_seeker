import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/providers.dart';
import '../theme.dart';
import '../widgets/common.dart';
import '../widgets/queue_banner.dart';

Widget _card(Widget child) => Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppTheme.card,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppTheme.borderSubtle),
      ),
      child: child,
    );

Widget _title(String t) => Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Text(t, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 14.5, color: AppTheme.textPrimary)),
    );

int _n(dynamic v) => v is num ? v.round() : 0;

/// Progress report: totals, the last seven days, jobs by stage, best jobs still waiting, recent emails.
class ReportScreen extends ConsumerWidget {
  const ReportScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final rep = ref.watch(trackerProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Progress report'),
        actions: [IconButton(icon: const Icon(Icons.refresh_rounded), onPressed: () => ref.invalidate(trackerProvider))],
      ),
      body: AsyncBody<Map<String, dynamic>>(
        value: rep,
        onRetry: () => ref.invalidate(trackerProvider),
        data: (r) {
          final totals = ((r['totals'] as Map?) ?? const {}).cast<String, dynamic>();
          final week = ((r['last_7_days'] as List?) ?? const []).cast<Map<String, dynamic>>();
          final jobs = ((r['jobs_by_status'] as Map?) ?? const {}).cast<String, dynamic>();
          final apps = ((r['applications_by_platform'] as Map?) ?? const {}).cast<String, dynamic>();
          final waiting = ((r['top_waiting_jobs'] as List?) ?? const []).cast<Map<String, dynamic>>();
          final emails = ((r['recent_emails'] as List?) ?? const []).cast<Map<String, dynamic>>();
          final maxDay = week.fold<int>(1, (m, d) => [m, _n(d['emails']), _n(d['applications'])].reduce((a, b) => a > b ? a : b));
          return RefreshIndicator(
            onRefresh: () => ref.refresh(trackerProvider.future),
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                _card(Row(children: [
                  _Stat('Emails sent', _n(totals['emails_sent']), AppTheme.secondaryLight),
                  _Stat('Applications', _n(totals['applications']), AppTheme.successLight),
                  _Stat('Jobs found', _n(totals['jobs_found']), AppTheme.primaryLight),
                  _Stat('HR contacts', _n(totals['hr_contacts']), AppTheme.warningLight),
                ])),
                _card(Row(children: [
                  _Stat('Emails today', _n(r['emails_sent_today']), AppTheme.secondaryLight),
                  _Stat('Applied today', _n(r['applications_today']), AppTheme.successLight),
                  _Stat('Send failures', _n(totals['emails_failed']), AppTheme.error),
                ])),
                _card(Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  _title('Last 7 days'),
                  SizedBox(
                    height: 110,
                    child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
                      for (final d in week)
                        Expanded(
                          child: Column(mainAxisAlignment: MainAxisAlignment.end, children: [
                            Row(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.end, children: [
                              _Bar(_n(d['emails']) / maxDay, AppTheme.secondary),
                              const SizedBox(width: 3),
                              _Bar(_n(d['applications']) / maxDay, AppTheme.success),
                            ]),
                            const SizedBox(height: 4),
                            Text((d['day'] as String).substring(8), style: const TextStyle(fontSize: 10.5, color: AppTheme.textMuted)),
                          ]),
                        ),
                    ]),
                  ),
                  const SizedBox(height: 6),
                  const Row(children: [
                    Icon(Icons.square_rounded, size: 10, color: AppTheme.secondary),
                    SizedBox(width: 4),
                    Text('emails', style: TextStyle(fontSize: 11.5, color: AppTheme.textSecondary)),
                    SizedBox(width: 12),
                    Icon(Icons.square_rounded, size: 10, color: AppTheme.success),
                    SizedBox(width: 4),
                    Text('applications', style: TextStyle(fontSize: 11.5, color: AppTheme.textSecondary)),
                  ]),
                ])),
                if (jobs.isNotEmpty || apps.isNotEmpty)
                  _card(Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    _title('Jobs by stage'),
                    Wrap(spacing: 8, runSpacing: 6, children: [
                      for (final e in jobs.entries) _CountChip(e.key, _n(e.value)),
                    ]),
                    if (apps.isNotEmpty) ...[
                      const SizedBox(height: 12),
                      _title('Applied by platform'),
                      Wrap(spacing: 8, runSpacing: 6, children: [for (final e in apps.entries) _CountChip(e.key, _n(e.value))]),
                    ],
                  ])),
                if (waiting.isNotEmpty)
                  _card(Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    _title('Best jobs still waiting'),
                    for (final j in waiting)
                      ListTile(
                        dense: true,
                        contentPadding: EdgeInsets.zero,
                        title: Text('${j['title']}', maxLines: 1, overflow: TextOverflow.ellipsis),
                        subtitle: Text('${j['company']}'),
                        trailing: Text('${_n(j['score'])}', style: const TextStyle(fontWeight: FontWeight.w800)),
                      ),
                    TextButton(onPressed: () => context.go('/jobs'), child: const Text('Open Jobs')),
                  ])),
                _card(Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  _title('Recent emails'),
                  if (emails.isEmpty) const Text('No emails sent yet.', style: TextStyle(color: AppTheme.textSecondary)),
                  for (final m in emails)
                    ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      title: Text('${m['to_email']}', maxLines: 1, overflow: TextOverflow.ellipsis),
                      subtitle: Text('${m['subject']}', maxLines: 1, overflow: TextOverflow.ellipsis),
                      trailing: StatusChip('${m['result']}'.startsWith('sent') ? 'sent' : 'failed', isMini: true),
                    ),
                ])),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat(this.label, this.value, this.color);
  final String label;
  final int value;
  final Color color;

  @override
  Widget build(BuildContext context) => Expanded(
        child: Column(children: [
          Text('$value', style: TextStyle(fontSize: 24, fontWeight: FontWeight.w800, color: color)),
          const SizedBox(height: 2),
          Text(label, textAlign: TextAlign.center, style: const TextStyle(fontSize: 11.5, color: AppTheme.textSecondary)),
        ]),
      );
}

class _Bar extends StatelessWidget {
  const _Bar(this.fraction, this.color);
  final double fraction;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
        width: 9,
        height: 4 + 72 * fraction.clamp(0.0, 1.0),
        decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(3)),
      );
}

class _CountChip extends StatelessWidget {
  const _CountChip(this.label, this.count);
  final String label;
  final int count;

  @override
  Widget build(BuildContext context) => Chip(
        visualDensity: VisualDensity.compact,
        label: Text('$label  $count'),
      );
}

/// Readiness checks with a fix button for each problem.
class StatusScreen extends ConsumerWidget {
  const StatusScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final st = ref.watch(statusProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('System status'),
        actions: [IconButton(icon: const Icon(Icons.refresh_rounded), onPressed: () => ref.invalidate(statusProvider))],
      ),
      body: AsyncBody<Map<String, dynamic>>(
        value: st,
        onRetry: () => ref.invalidate(statusProvider),
        data: (s) {
          final creds = ((s['credentials'] as Map?) ?? const {}).cast<String, dynamic>();
          final sessions = ((s['login_sessions_saved'] as Map?) ?? const {}).cast<String, dynamic>();
          final resume = ((s['resume'] as Map?) ?? const {}).cast<String, dynamic>();
          final llm = ((s['llm'] as Map?) ?? const {}).cast<String, dynamic>();
          final ap = ((s['autopilot'] as Map?) ?? const {}).cast<String, dynamic>();
          final today = ((s['today'] as Map?) ?? const {}).cast<String, dynamic>();
          final caps = ((today['caps'] as Map?) ?? const {}).cast<String, dynamic>();
          final missing = ((s['missing_profile_fields'] as List?) ?? const []).cast<String>();
          final runs = ((s['runs'] as Map?) ?? const {}).cast<String, dynamic>();
          final cool = ((ap['cooldowns'] as Map?) ?? const {}).cast<String, dynamic>();
          final rows = <_Check>[
            _Check('Resume', resume['exists'] == true,
                resume['exists'] == true ? '${resume['original_filename'] ?? 'resume.pdf'} · ${_n(resume['bytes'])} bytes' : 'Upload it in Settings',
                resume['exists'] == true ? null : () => context.go('/settings')),
            _Check('Profile', missing.isEmpty, missing.isEmpty ? 'Complete' : 'Missing: ${missing.join(', ')}', missing.isEmpty ? null : () => context.go('/settings')),
            _Check('Gmail (sending)', creds['gmail_app_password'] == true, '${creds['gmail_user'] ?? ''}',
                creds['gmail_app_password'] == true ? null : () => context.go('/settings')),
            _Check('LinkedIn session', sessions['linkedin'] == true, sessions['linkedin'] == true ? 'Linked' : 'Not linked',
                () => context.push('/tools/login_platform', extra: {'platform': 'linkedin'})),
            _Check('Naukri session', sessions['naukri'] == true, sessions['naukri'] == true ? 'Linked' : 'Not linked',
                () => context.push('/tools/login_platform', extra: {'platform': 'naukri'})),
            _Check('Indeed session', sessions['indeed'] == true, sessions['indeed'] == true ? 'Linked' : 'Not linked (optional)',
                () => context.push('/tools/login_platform', extra: {'platform': 'indeed'}),
                optional: true),
            _Check('AI post analysis', llm['ready'] == true, llm['ready'] == true ? '${llm['provider']} · ${llm['model']}' : '${llm['reason'] ?? 'off'}',
                llm['ready'] == true ? null : () => context.go('/settings'), optional: true),
            _Check('Live actions', s['real_actions_allowed'] == true,
                s['real_actions_allowed'] == true ? 'Allowed (emails and applications are real)' : '${s['real_actions_blocked_reason'] ?? 'blocked'}',
                s['real_actions_allowed'] == true ? null : () => context.go('/settings'), optional: true),
            _Check('Browser (Chromium)', s['chromium_installed'] == true, s['chromium_installed'] == true ? 'Installed' : 'Missing on the server', null),
            _Check('Kill switch', ap['stopped'] != true, ap['stopped'] == true ? 'ON: everything is stopped' : 'Off', null),
            _Check('Platform cooldowns', cool.isEmpty, cool.isEmpty ? 'None' : cool.entries.map((e) => '${e.key}: ${e.value}').join('; '), null),
          ];
          return RefreshIndicator(
            onRefresh: () => ref.refresh(statusProvider.future),
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const QueueBanner(),
                _card(Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  _title('Today'),
                  Row(children: [
                    _Stat('Applications  (cap ${_n(caps['applications'])})', _n(today['applications']), AppTheme.successLight),
                    _Stat('Emails  (cap ${_n(caps['emails'])})', _n(today['emails_sent']), AppTheme.secondaryLight),
                    _Stat('Running now  (max ${_n(runs['max_parallel'])})', ((runs['running'] as List?) ?? const []).length, AppTheme.primaryLight),
                  ]),
                ])),
                _card(Column(children: [for (final c in rows) c])),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _Check extends StatelessWidget {
  const _Check(this.label, this.ok, this.detail, this.fix, {this.optional = false});
  final String label;
  final bool ok;
  final String detail;
  final VoidCallback? fix;
  final bool optional;

  @override
  Widget build(BuildContext context) {
    final color = ok ? AppTheme.success : (optional ? AppTheme.warning : AppTheme.error);
    return ListTile(
      dense: true,
      contentPadding: EdgeInsets.zero,
      leading: Icon(ok ? Icons.check_circle_rounded : (optional ? Icons.info_rounded : Icons.error_rounded), color: color),
      title: Text(label, style: const TextStyle(fontWeight: FontWeight.w600)),
      subtitle: Text(detail, maxLines: 2, overflow: TextOverflow.ellipsis),
      trailing: !ok && fix != null ? TextButton(onPressed: fix, child: const Text('Fix')) : null,
    );
  }
}
