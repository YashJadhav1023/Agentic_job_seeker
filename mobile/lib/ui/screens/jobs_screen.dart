import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../state/providers.dart';
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

  @override
  Widget build(BuildContext context) {
    final jobs = ref.watch(jobsProvider(_f));
    return Scaffold(
      appBar: AppBar(title: const Text('Jobs'), actions: [
        IconButton(
          tooltip: 'Find more jobs',
          icon: const Icon(Icons.travel_explore),
          onPressed: () => context.push('/tools/multi_board_search', extra: {'save': true}),
        ),
      ]),
      body: Column(children: [
        SizedBox(
          height: 56,
          child: ListView(scrollDirection: Axis.horizontal, padding: const EdgeInsets.symmetric(horizontal: 12), children: [
            for (final s in _statuses)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 8),
                child: ChoiceChip(
                  label: Text(s ?? 'all'),
                  selected: _f.status == s,
                  onSelected: (_) => setState(() => _f = JobFilter(status: s, minScore: _f.minScore)),
                ),
              ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 8),
              child: FilterChip(
                label: const Text('score ≥ 45'),
                selected: _f.minScore != null,
                onSelected: (on) => setState(() => _f = JobFilter(status: _f.status, minScore: on ? 45 : null)),
              ),
            ),
          ]),
        ),
        Expanded(
          child: AsyncBody<Map<String, dynamic>>(
            value: jobs,
            onRetry: () => ref.invalidate(jobsProvider(_f)),
            data: (d) {
              final list = ((d['jobs'] as List?) ?? const []).cast<Map<String, dynamic>>();
              if (list.isEmpty) {
                return Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    const Icon(Icons.work_off_outlined, size: 48),
                    const SizedBox(height: 8),
                    const Text('No jobs yet'),
                    const SizedBox(height: 12),
                    FilledButton.icon(
                      onPressed: () => context.push('/tools/multi_board_search', extra: {'save': true}),
                      icon: const Icon(Icons.travel_explore),
                      label: const Text('Search all job boards'),
                    ),
                  ]),
                );
              }
              return RefreshIndicator(
                onRefresh: () => ref.refresh(jobsProvider(_f).future),
                child: ListView.separated(
                  itemCount: list.length,
                  separatorBuilder: (_, _) => const Divider(height: 1),
                  itemBuilder: (c, i) => _JobTile(list[i]),
                ),
              );
            },
          ),
        ),
      ]),
    );
  }
}

class _JobTile extends StatelessWidget {
  const _JobTile(this.j);
  final Map<String, dynamic> j;

  @override
  Widget build(BuildContext context) {
    final score = (j['score'] as num?)?.round();
    return ListTile(
      leading: CircleAvatar(child: Text(score == null ? '–' : '$score')),
      title: Text('${j['title']}'),
      subtitle: Text('${j['company']} · ${j['location'] ?? ''} · ${j['source']}', maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: StatusChip('${j['status']}'),
      onTap: () => showModalBottomSheet(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (_) => _JobSheet(j),
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
    void go(String tool, Map<String, dynamic> args) {
      Navigator.pop(context);
      context.push('/tools/$tool', extra: args);
    }

    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.6,
      builder: (c, sc) => ListView(controller: sc, padding: const EdgeInsets.fromLTRB(20, 0, 20, 24), children: [
        Text('${j['title']}', style: Theme.of(context).textTheme.titleLarge),
        Text('${j['company']} · ${j['location'] ?? ''}'),
        const SizedBox(height: 8),
        Wrap(spacing: 8, runSpacing: 4, children: [
          StatusChip('${j['status']}'),
          if (j['score'] != null) Chip(label: Text('match ${(j['score'] as num).round()}')),
          for (final m in matched) Chip(label: Text(m), visualDensity: VisualDensity.compact),
        ]),
        if ((j['reason'] ?? '').toString().isNotEmpty) Padding(padding: const EdgeInsets.only(top: 8), child: Text('${j['reason']}')),
        if ((j['contact'] ?? '').toString().isNotEmpty)
          Padding(padding: const EdgeInsets.only(top: 8), child: Text('Contact: ${j['contact']}')),
        const SizedBox(height: 16),
        if (j['url'] != null && '${j['url']}'.startsWith('http'))
          OutlinedButton.icon(
            onPressed: () => launchUrl(Uri.parse('${j['url']}'), mode: LaunchMode.externalApplication),
            icon: const Icon(Icons.open_in_new),
            label: const Text('Open posting'),
          ),
        const SizedBox(height: 8),
        FilledButton.icon(onPressed: () => go('compose_email', {'job_id': id}), icon: const Icon(Icons.edit_note),
            label: const Text('Draft application email')),
        const SizedBox(height: 8),
        FilledButton.tonalIcon(onPressed: () => go('find_contact', {'job_id': id}), icon: const Icon(Icons.alternate_email),
            label: const Text('Find who to email')),
        const SizedBox(height: 8),
        FilledButton.tonalIcon(onPressed: () => go('generate_linkedin_pitch', {'job_id': id}),
            icon: const Icon(Icons.handshake_outlined), label: const Text('LinkedIn connection pitch')),
        const SizedBox(height: 8),
        FilledButton.tonalIcon(onPressed: () => go('find_company_emails', {'company': j['company']}),
            icon: const Icon(Icons.domain), label: const Text('Company email discovery')),
        const SizedBox(height: 8),
        if (j['source'] == 'linkedin')
          FilledButton.tonalIcon(onPressed: () => go('job_detail', {'id': j['url']}), icon: const Icon(Icons.description_outlined),
              label: const Text('Full job description')),
      ]),
    );
  }
}
