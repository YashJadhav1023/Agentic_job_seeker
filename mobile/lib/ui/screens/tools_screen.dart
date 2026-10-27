import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../models/tool_spec.dart';
import '../../state/providers.dart';
import '../theme.dart';
import '../widgets/common.dart';

/// Modern Tools Catalogue grouped by category with search and safety indicators.
class ToolsScreen extends ConsumerStatefulWidget {
  const ToolsScreen({super.key});

  @override
  ConsumerState<ToolsScreen> createState() => _ToolsScreenState();
}

class _ToolsScreenState extends ConsumerState<ToolsScreen> {
  String _q = '';
  String? _selectedCategory;

  static const _icons = {
    'Status': Icons.monitor_heart_rounded,
    'Discover jobs': Icons.travel_explore_rounded,
    'Apply': Icons.assignment_turned_in_rounded,
    'Autopilot': Icons.smart_toy_rounded,
    'Find contacts': Icons.contact_mail_rounded,
    'Outreach': Icons.forward_to_inbox_rounded,
    'Other': Icons.extension_rounded,
  };

  @override
  Widget build(BuildContext context) {
    final tools = ref.watch(toolsProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('All Tools (32)'),
        actions: [
          IconButton(
            tooltip: 'Refresh tools',
            icon: const Icon(Icons.refresh_rounded),
            onPressed: () => ref.invalidate(toolsProvider),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: AsyncBody<List<ToolSpec>>(
        value: tools,
        onRetry: () => ref.invalidate(toolsProvider),
        data: (list) {
          final categories = categoryOrder.where((c) => list.any((t) => t.category == c)).toList();

          var filtered = list.where((t) {
            final matchesQuery = _q.isEmpty ||
                t.name.toLowerCase().contains(_q) ||
                t.title.toLowerCase().contains(_q) ||
                t.description.toLowerCase().contains(_q);
            final matchesCat = _selectedCategory == null || t.category == _selectedCategory;
            return matchesQuery && matchesCat;
          }).toList();

          return Column(
            children: [
              // Search Bar
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
                child: TextField(
                  decoration: InputDecoration(
                    hintText: 'Search 32 tools by name or description...',
                    prefixIcon: const Icon(Icons.search_rounded, size: 20),
                    suffixIcon: _q.isNotEmpty
                        ? IconButton(
                            icon: const Icon(Icons.clear_rounded, size: 18),
                            onPressed: () => setState(() => _q = ''),
                          )
                        : null,
                    contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                  ),
                  onChanged: (v) => setState(() => _q = v.trim().toLowerCase()),
                ),
              ),
              // Category Filter Chips
              SizedBox(
                height: 44,
                child: ListView(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  children: [
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 4),
                      child: ChoiceChip(
                        label: Text('All (${list.length})'),
                        selected: _selectedCategory == null,
                        onSelected: (_) => setState(() => _selectedCategory = null),
                      ),
                    ),
                    for (final c in categories)
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 4),
                        child: ChoiceChip(
                          avatar: Icon(_icons[c] ?? Icons.extension_rounded, size: 14),
                          label: Text(c),
                          selected: _selectedCategory == c,
                          onSelected: (on) => setState(() => _selectedCategory = on ? c : null),
                        ),
                      ),
                  ],
                ),
              ),
              const SizedBox(height: 6),
              // Tool List
              Expanded(
                child: filtered.isEmpty
                    ? Center(
                        child: Text(
                          'No tools found for "$_q"',
                          style: const TextStyle(color: AppTheme.textMuted),
                        ),
                      )
                    : RefreshIndicator(
                        onRefresh: () => ref.refresh(toolsProvider.future),
                        child: ListView.separated(
                          padding: const EdgeInsets.fromLTRB(16, 8, 16, 80),
                          itemCount: filtered.length,
                          separatorBuilder: (_, _) => const SizedBox(height: 8),
                          itemBuilder: (c, i) => _ToolTile(filtered[i]),
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

class _ToolTile extends StatelessWidget {
  const _ToolTile(this.t);
  final ToolSpec t;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      borderRadius: BorderRadius.circular(14),
      onTap: () => context.push('/tools/${t.name}'),
      child: Container(
        decoration: BoxDecoration(
          color: AppTheme.card,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: AppTheme.borderSubtle),
        ),
        padding: const EdgeInsets.all(14),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Flexible(
                        child: Text(
                          t.title,
                          style: const TextStyle(
                            fontSize: 14.5,
                            fontWeight: FontWeight.w700,
                            color: AppTheme.textPrimary,
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                        decoration: BoxDecoration(
                          color: AppTheme.surface,
                          borderRadius: BorderRadius.circular(6),
                          border: Border.all(color: AppTheme.borderSubtle),
                        ),
                        child: Text(
                          t.category,
                          style: const TextStyle(fontSize: 10.5, color: AppTheme.textMuted, fontWeight: FontWeight.w500),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Text(
                    t.description,
                    style: const TextStyle(fontSize: 12, color: AppTheme.textSecondary, height: 1.3),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
            const SizedBox(width: 10),
            if (t.destructive)
              Container(
                padding: const EdgeInsets.all(6),
                decoration: BoxDecoration(
                  color: AppTheme.warning.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: const Icon(Icons.bolt_rounded, size: 16, color: AppTheme.warningLight),
              )
            else
              const Icon(Icons.chevron_right_rounded, size: 20, color: AppTheme.textMuted),
          ],
        ),
      ),
    );
  }
}
