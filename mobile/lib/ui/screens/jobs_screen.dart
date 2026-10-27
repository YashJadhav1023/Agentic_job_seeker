import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../state/providers.dart';
import '../theme.dart';
import '../widgets/common.dart';

const _statuses = [null, 'new', 'scored', 'queued', 'emailed', 'no_contact', 'skip', 'error'];

/// Jobs discovered by autopilot / multi-board search, best match first.
class JobsScreen extends ConsumerStatefulWidget {
  const JobsScreen({super.key});

  @override
  ConsumerState<JobsScreen> createState() => _JobsScreenState();
}

class _JobsScreenState extends ConsumerState<JobsScreen> {
  JobFilter _f = const JobFilter();
  String _searchQuery = '';

  @override
  Widget build(BuildContext context) {
    final jobs = ref.watch(jobsProvider(_f));
    return Scaffold(
      appBar: AppBar(
        title: const Text('Discovered Jobs'),
        actions: [
          IconButton(
            tooltip: 'Search all job boards',
            icon: const Icon(Icons.travel_explore_rounded),
            onPressed: () => context.push('/tools/multi_board_search', extra: {'save': true}),
          ),
          IconButton(
            tooltip: 'Refresh jobs',
            icon: const Icon(Icons.refresh_rounded),
            onPressed: () => ref.invalidate(jobsProvider(_f)),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: Column(
        children: [
          // Search & Filter Box
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
            child: TextField(
              decoration: InputDecoration(
                hintText: 'Search title, company, or skills...',
                prefixIcon: const Icon(Icons.search_rounded, size: 20),
                suffixIcon: _searchQuery.isNotEmpty
                    ? IconButton(
                        icon: const Icon(Icons.clear_rounded, size: 18),
                        onPressed: () => setState(() => _searchQuery = ''),
                      )
                    : null,
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              ),
              onChanged: (v) => setState(() => _searchQuery = v.trim().toLowerCase()),
            ),
          ),
          // Horizontal Filter Chips
          SizedBox(
            height: 48,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              children: [
                for (final s in _statuses)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
                    child: ChoiceChip(
                      label: Text(s == null ? 'All Jobs' : s[0].toUpperCase() + s.substring(1)),
                      selected: _f.status == s,
                      onSelected: (_) => setState(() => _f = JobFilter(status: s, minScore: _f.minScore)),
                    ),
                  ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
                  child: FilterChip(
                    avatar: const Icon(Icons.auto_awesome, size: 14, color: AppTheme.successLight),
                    label: const Text('Score ≥ 45'),
                    selected: _f.minScore != null,
                    onSelected: (on) => setState(() => _f = JobFilter(status: _f.status, minScore: on ? 45 : null)),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 4),
          // Jobs List View
          Expanded(
            child: AsyncBody<Map<String, dynamic>>(
              value: jobs,
              onRetry: () => ref.invalidate(jobsProvider(_f)),
              data: (d) {
                var list = ((d['jobs'] as List?) ?? const []).cast<Map<String, dynamic>>();

                if (_searchQuery.isNotEmpty) {
                  list = list.where((j) {
                    final title = '${j['title']}'.toLowerCase();
                    final company = '${j['company']}'.toLowerCase();
                    final loc = '${j['location']}'.toLowerCase();
                    final matched = ((j['matched'] as List?) ?? const []).join(' ').toLowerCase();
                    return title.contains(_searchQuery) ||
                        company.contains(_searchQuery) ||
                        loc.contains(_searchQuery) ||
                        matched.contains(_searchQuery);
                  }).toList();
                }

                if (list.isEmpty) {
                  return Center(
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
                            child: const Icon(Icons.work_off_outlined, size: 40, color: AppTheme.textMuted),
                          ),
                          const SizedBox(height: 16),
                          Text(
                            _searchQuery.isNotEmpty ? 'No jobs match your search' : 'No jobs found',
                            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: AppTheme.textPrimary),
                          ),
                          const SizedBox(height: 6),
                          const Text(
                            'Run a multi-board search to discover live jobs from LinkedIn, Indeed & Glassdoor.',
                            textAlign: TextAlign.center,
                            style: TextStyle(fontSize: 13, color: AppTheme.textSecondary),
                          ),
                          const SizedBox(height: 18),
                          FilledButton.icon(
                            onPressed: () => context.push('/tools/multi_board_search', extra: {'save': true}),
                            icon: const Icon(Icons.travel_explore_rounded, size: 18),
                            label: const Text('Search All Job Boards'),
                          ),
                        ],
                      ),
                    ),
                  );
                }

                return RefreshIndicator(
                  onRefresh: () => ref.refresh(jobsProvider(_f).future),
                  child: ListView.separated(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 80),
                    itemCount: list.length,
                    separatorBuilder: (_, _) => const SizedBox(height: 10),
                    itemBuilder: (c, i) => _JobCard(list[i]),
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _JobCard extends StatelessWidget {
  const _JobCard(this.j);
  final Map<String, dynamic> j;

  @override
  Widget build(BuildContext context) {
    final score = (j['score'] as num?)?.round();
    final matched = ((j['matched'] as List?) ?? const []).map((e) => '$e').take(4).toList();
    final status = '${j['status']}';

    final (scoreBg, scoreFg) = switch (score) {
      null => (AppTheme.surface, AppTheme.textMuted),
      >= 70 => (AppTheme.success.withValues(alpha: 0.18), AppTheme.successLight),
      >= 40 => (AppTheme.warning.withValues(alpha: 0.18), AppTheme.warningLight),
      _ => (AppTheme.cardElevated, AppTheme.textSecondary),
    };

    return InkWell(
      borderRadius: BorderRadius.circular(16),
      onTap: () => showModalBottomSheet(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (_) => _JobSheet(j),
      ),
      child: Container(
        decoration: BoxDecoration(
          color: AppTheme.card,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppTheme.borderSubtle),
        ),
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${j['title']}',
                        style: const TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w700,
                          color: AppTheme.textPrimary,
                          letterSpacing: -0.2,
                        ),
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                      ),
                      const SizedBox(height: 4),
                      Row(
                        children: [
                          const Icon(Icons.business_rounded, size: 14, color: AppTheme.textMuted),
                          const SizedBox(width: 4),
                          Expanded(
                            child: Text(
                              '${j['company']}',
                              style: const TextStyle(
                                fontSize: 13,
                                fontWeight: FontWeight.w600,
                                color: AppTheme.textSecondary,
                              ),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 10),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                  decoration: BoxDecoration(
                    color: scoreBg,
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: scoreFg.withValues(alpha: 0.3), width: 0.8),
                  ),
                  child: Column(
                    children: [
                      Text(
                        score == null ? '–' : '$score',
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w800,
                          color: scoreFg,
                        ),
                      ),
                      Text(
                        'MATCH',
                        style: TextStyle(
                          fontSize: 9,
                          fontWeight: FontWeight.w700,
                          color: scoreFg,
                          letterSpacing: 0.4,
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                if ((j['location'] ?? '').toString().isNotEmpty) ...[
                  const Icon(Icons.location_on_outlined, size: 13, color: AppTheme.textMuted),
                  const SizedBox(width: 3),
                  Text(
                    '${j['location']}',
                    style: const TextStyle(fontSize: 12, color: AppTheme.textMuted),
                  ),
                  const SizedBox(width: 12),
                ],
                const Icon(Icons.language_rounded, size: 13, color: AppTheme.textMuted),
                const SizedBox(width: 3),
                Text(
                  '${j['source'] ?? 'board'}',
                  style: const TextStyle(fontSize: 12, color: AppTheme.textMuted),
                ),
                const Spacer(),
                StatusChip(status, isMini: true),
              ],
            ),
            if (matched.isNotEmpty) ...[
              const SizedBox(height: 10),
              Wrap(
                spacing: 6,
                runSpacing: 4,
                children: [
                  for (final m in matched)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                      decoration: BoxDecoration(
                        color: AppTheme.primaryContainer.withValues(alpha: 0.4),
                        borderRadius: BorderRadius.circular(6),
                        border: Border.all(color: AppTheme.primary.withValues(alpha: 0.2)),
                      ),
                      child: Text(
                        m,
                        style: const TextStyle(color: AppTheme.primaryLight, fontSize: 11, fontWeight: FontWeight.w500),
                      ),
                    ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _JobSheet extends StatelessWidget {
  const _JobSheet(this.j);
  final Map<String, dynamic> j;

  @override
  Widget build(BuildContext context) {
    final id = j['id'];
    final matched = ((j['matched'] as List?) ?? const []).map((e) => '$e').toList();
    final score = (j['score'] as num?)?.round();

    void go(String tool, Map<String, dynamic> args) {
      Navigator.pop(context);
      context.push('/tools/$tool', extra: args);
    }

    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.75,
      maxChildSize: 0.92,
      builder: (c, sc) => ListView(
        controller: sc,
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${j['title']}',
                      style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800, color: AppTheme.textPrimary),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '${j['company']} · ${j['location'] ?? ''}',
                      style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500, color: AppTheme.textSecondary),
                    ),
                  ],
                ),
              ),
              StatusChip('${j['status']}'),
            ],
          ),
          const SizedBox(height: 14),
          if (score != null) ...[
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppTheme.card,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: AppTheme.borderSubtle),
              ),
              child: Row(
                children: [
                  const Icon(Icons.stars_rounded, color: AppTheme.successLight, size: 20),
                  const SizedBox(width: 8),
                  Text(
                    'Fit Score: $score / 100',
                    style: const TextStyle(fontWeight: FontWeight.w700, color: AppTheme.textPrimary, fontSize: 14),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),
          ],
          if (matched.isNotEmpty) ...[
            const Text('Matched Skills', style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: AppTheme.textSecondary)),
            const SizedBox(height: 6),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final m in matched)
                  Chip(
                    label: Text(m),
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    backgroundColor: AppTheme.card,
                  ),
              ],
            ),
            const SizedBox(height: 14),
          ],
          if ((j['reason'] ?? '').toString().isNotEmpty) ...[
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppTheme.card,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(
                'Evaluation Note: ${j['reason']}',
                style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12.5),
              ),
            ),
            const SizedBox(height: 14),
          ],
          const Divider(),
          const SizedBox(height: 14),
          const Text('Actions', style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: AppTheme.textPrimary)),
          const SizedBox(height: 10),
          FilledButton.icon(
            style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 13)),
            onPressed: () => go('compose_email', {'job_id': id}),
            icon: const Icon(Icons.edit_note_rounded, size: 20),
            label: const Text('Draft Application Email'),
          ),
          const SizedBox(height: 8),
          FilledButton.tonalIcon(
            onPressed: () => go('find_contact', {'job_id': id}),
            icon: const Icon(Icons.alternate_email_rounded, size: 18),
            label: const Text('Find Who to Email'),
          ),
          const SizedBox(height: 8),
          FilledButton.tonalIcon(
            onPressed: () => go('generate_linkedin_pitch', {'job_id': id}),
            icon: const Icon(Icons.handshake_outlined, size: 18),
            label: const Text('Generate LinkedIn Pitch'),
          ),
          const SizedBox(height: 8),
          if (j['url'] != null && '${j['url']}'.startsWith('http'))
            OutlinedButton.icon(
              onPressed: () => launchUrl(Uri.parse('${j['url']}'), mode: LaunchMode.externalApplication),
              icon: const Icon(Icons.open_in_new_rounded, size: 16),
              label: const Text('Open Original Job Posting'),
            ),
        ],
      ),
    );
  }
}
