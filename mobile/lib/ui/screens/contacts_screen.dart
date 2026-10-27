import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/providers.dart';
import '../theme.dart';
import '../widgets/common.dart';

/// Modern HR & Recruiter contacts directory with quick email, copy, and verification.
class ContactsScreen extends ConsumerStatefulWidget {
  const ContactsScreen({super.key});

  @override
  ConsumerState<ContactsScreen> createState() => _ContactsScreenState();
}

class _ContactsScreenState extends ConsumerState<ContactsScreen> {
  String _query = '';
  String? _filterConfidence; // null for all, 'high', 'low'

  @override
  Widget build(BuildContext context) {
    final contacts = ref.watch(contactsProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Recruiter Directory'),
        actions: [
          IconButton(
            tooltip: 'Add contact manually',
            icon: const Icon(Icons.person_add_alt_1_rounded),
            onPressed: () => context.push('/tools/add_hr_contact'),
          ),
          IconButton(
            tooltip: 'Refresh contacts',
            icon: const Icon(Icons.refresh_rounded),
            onPressed: () => ref.invalidate(contactsProvider),
          ),
          const SizedBox(width: 8),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: AppTheme.primary,
        foregroundColor: Colors.white,
        elevation: 3,
        onPressed: () => context.push('/tools/send_hr_emails', extra: {'dry_run': true}),
        icon: const Icon(Icons.forward_to_inbox_rounded),
        label: const Text('Email Best Contacts', style: TextStyle(fontWeight: FontWeight.w700)),
      ),
      body: Column(
        children: [
          // Search Bar
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
            child: TextField(
              decoration: InputDecoration(
                hintText: 'Search by email, company, or role...',
                prefixIcon: const Icon(Icons.search_rounded, size: 20),
                suffixIcon: _query.isNotEmpty
                    ? IconButton(
                        icon: const Icon(Icons.clear_rounded, size: 18),
                        onPressed: () => setState(() => _query = ''),
                      )
                    : null,
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              ),
              onChanged: (v) => setState(() => _query = v.trim().toLowerCase()),
            ),
          ),
          // Filter Chips
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 2),
            child: Row(
              children: [
                ChoiceChip(
                  label: const Text('All Contacts'),
                  selected: _filterConfidence == null,
                  onSelected: (_) => setState(() => _filterConfidence = null),
                ),
                const SizedBox(width: 8),
                ChoiceChip(
                  avatar: const Icon(Icons.verified_rounded, size: 14, color: AppTheme.successLight),
                  label: const Text('High Fit'),
                  selected: _filterConfidence == 'high',
                  onSelected: (on) => setState(() => _filterConfidence = on ? 'high' : null),
                ),
                const SizedBox(width: 8),
                ChoiceChip(
                  label: const Text('General'),
                  selected: _filterConfidence == 'low',
                  onSelected: (on) => setState(() => _filterConfidence = on ? 'low' : null),
                ),
              ],
            ),
          ),
          const SizedBox(height: 6),
          // Contacts List
          Expanded(
            child: AsyncBody<List<Map<String, dynamic>>>(
              value: contacts,
              onRetry: () => ref.invalidate(contactsProvider),
              data: (all) {
                var list = all;

                if (_filterConfidence != null) {
                  list = list.where((c) => '${c['confidence']}'.toLowerCase() == _filterConfidence).toList();
                }

                if (_query.isNotEmpty) {
                  list = list.where((c) {
                    final email = '${c['email']}'.toLowerCase();
                    final company = '${c['company']}'.toLowerCase();
                    final role = '${c['role']}'.toLowerCase();
                    final city = '${c['city']}'.toLowerCase();
                    return email.contains(_query) ||
                        company.contains(_query) ||
                        role.contains(_query) ||
                        city.contains(_query);
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
                            child: const Icon(Icons.people_outline_rounded, size: 40, color: AppTheme.textMuted),
                          ),
                          const SizedBox(height: 16),
                          Text(
                            _query.isNotEmpty ? 'No recruiters match your search' : 'No HR contacts yet',
                            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: AppTheme.textPrimary),
                          ),
                          const SizedBox(height: 6),
                          const Text(
                            'Discover verified HR & talent acquisition emails for target companies in your cities.',
                            textAlign: TextAlign.center,
                            style: TextStyle(fontSize: 13, color: AppTheme.textSecondary),
                          ),
                          const SizedBox(height: 18),
                          FilledButton.icon(
                            onPressed: () => context.push('/tools/find_hr_emails'),
                            icon: const Icon(Icons.person_search_rounded, size: 18),
                            label: const Text('Discover HR Contacts'),
                          ),
                        ],
                      ),
                    ),
                  );
                }

                return RefreshIndicator(
                  onRefresh: () => ref.refresh(contactsProvider.future),
                  child: ListView.separated(
                    padding: const EdgeInsets.fromLTRB(16, 6, 16, 90),
                    itemCount: list.length,
                    separatorBuilder: (_, _) => const SizedBox(height: 10),
                    itemBuilder: (c, i) => _ContactCard(list[i]),
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

class _ContactCard extends StatelessWidget {
  const _ContactCard(this.ct);
  final Map<String, dynamic> ct;

  String _initials(String email, String? company) {
    if (company != null && company.trim().isNotEmpty) {
      final parts = company.trim().split(RegExp(r'\s+'));
      if (parts.length >= 2) return '${parts[0][0]}${parts[1][0]}'.toUpperCase();
      return company.substring(0, company.length >= 2 ? 2 : 1).toUpperCase();
    }
    return email.substring(0, 2).toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    final email = '${ct['email']}';
    final company = ct['company']?.toString() ?? '';
    final role = ct['role']?.toString() ?? '';
    final city = ct['city']?.toString() ?? '';
    final confidence = '${ct['confidence']}';
    final initials = _initials(email, company);

    return Container(
      decoration: BoxDecoration(
        color: AppTheme.card,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppTheme.borderSubtle),
      ),
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 38,
                height: 38,
                decoration: BoxDecoration(
                  gradient: const LinearGradient(
                    colors: [Color(0xFF4338CA), Color(0xFF312E81)],
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                  ),
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: AppTheme.primaryLight.withValues(alpha: 0.3)),
                ),
                alignment: Alignment.center,
                child: Text(
                  initials,
                  style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w800, fontSize: 13),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      email,
                      style: const TextStyle(
                        fontSize: 14,
                        fontWeight: FontWeight.w700,
                        color: AppTheme.textPrimary,
                        letterSpacing: -0.2,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 2),
                    if (company.isNotEmpty)
                      Text(
                        company,
                        style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600, color: AppTheme.textSecondary),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                  ],
                ),
              ),
              StatusChip(confidence, isMini: true),
            ],
          ),
          if (role.isNotEmpty || city.isNotEmpty) ...[
            const SizedBox(height: 8),
            Row(
              children: [
                if (role.isNotEmpty) ...[
                  const Icon(Icons.badge_outlined, size: 13, color: AppTheme.textMuted),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      role,
                      style: const TextStyle(fontSize: 12, color: AppTheme.textMuted),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
                if (city.isNotEmpty) ...[
                  const SizedBox(width: 8),
                  const Icon(Icons.location_on_outlined, size: 13, color: AppTheme.textMuted),
                  const SizedBox(width: 2),
                  Text(
                    city,
                    style: const TextStyle(fontSize: 12, color: AppTheme.textMuted),
                  ),
                ],
              ],
            ),
          ],
          const SizedBox(height: 10),
          const Divider(height: 1),
          const SizedBox(height: 6),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              IconButton(
                visualDensity: VisualDensity.compact,
                tooltip: 'Copy email',
                icon: const Icon(Icons.copy_rounded, size: 16, color: AppTheme.textSecondary),
                onPressed: () {
                  Clipboard.setData(ClipboardData(text: email));
                  ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Copied $email')));
                },
              ),
              IconButton(
                visualDensity: VisualDensity.compact,
                tooltip: 'Verify deliverability',
                icon: const Icon(Icons.verified_user_outlined, size: 16, color: AppTheme.secondaryLight),
                onPressed: () => context.push('/tools/verify_email', extra: {'email': email}),
              ),
              TextButton.icon(
                style: TextButton.styleFrom(visualDensity: VisualDensity.compact),
                onPressed: () => context.push('/tools/send_email', extra: {'to': email, 'dry_run': true}),
                icon: const Icon(Icons.mail_outline_rounded, size: 16),
                label: const Text('Draft Email'),
              ),
              PopupMenuButton<String>(
                icon: const Icon(Icons.more_vert_rounded, size: 18, color: AppTheme.textMuted),
                onSelected: (v) {
                  if (v == 'suppress') {
                    context.push('/tools/suppress_contact', extra: {'email_or_domain': email});
                  }
                },
                itemBuilder: (_) => const [
                  PopupMenuItem(
                    value: 'suppress',
                    child: Row(
                      children: [
                        Icon(Icons.block_rounded, size: 16, color: AppTheme.error),
                        SizedBox(width: 8),
                        Text('Never email again', style: TextStyle(color: AppTheme.error)),
                      ],
                    ),
                  ),
                ],
              ),
            ],
          ),
        ],
      ),
    );
  }
}
