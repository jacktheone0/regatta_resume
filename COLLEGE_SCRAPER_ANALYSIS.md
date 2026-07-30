# College vs High School Scraper Comparison

## Summary: ✅ **The pattern WILL WORK with minor modifications**

The existing high school scraper framework can be adapted for college sailing with minimal changes. Both sites follow the same basic structure.

---

## 1. Schools Index Page Structure

### High School (scores.hssailing.org/schools/)
```html
<h3><a href="/districts/massa/">MASSA District</a></h3>
<table>
  <tr>
    <td><img src="mascot.png"/></td>
    <td><a href="/schools/annapolis/">Annapolis High School</a></td>
    <td>Annapolis</td>
    <td>MD</td>
  </tr>
</table>
```

**Data available:**
- District name (in H3)
- School name (link text)
- URL slug (from href: `/schools/annapolis/` → `annapolis`)
- City
- State
- Mascot (optional image)

**Current scraper:** ✅ Works (lines 23-55 in school_scraper.py)
- Finds all H3 headers
- Gets next sibling container
- Extracts links with `/schools/` in href

---

### College (scores.collegesailing.org/schools/)
```html
<h3><a href="/conferences/neisa/">NEISA Conference</a></h3>
<table>
  <tr>
    <td>Tufts</td>
    <td><a href="/schools/tufts/">Tufts University</a></td>
    <td>Medford</td>
    <td>MA</td>
  </tr>
</table>
```

**Data available:**
- Conference name (in H3) ← Different from "district"
- **Mascot name** (1st column - text, not image) ← NEW FIELD
- School name (link text)
- URL slug (from href: `/schools/tufts/` → `tufts`)
- City (sometimes empty)
- State (sometimes empty)

**Required changes:** 🔧 Minor
- Change "District" → "Conference" in column names
- Add mascot extraction from 1st `<td>` (not `<img>`)
- Handle empty city/state gracefully

---

## 2. Individual School Page Structure

### High School
**URL:** `https://scores.hssailing.org/schools/annapolis/`

**Available data:**
- School name
- District
- Current season roster link: `/schools/annapolis/s26/roster/`
- Season selector (f25, s25, f24, s24, etc.)
- Number of regattas
- **No regatta results on this page** (must visit roster page)

---

### College
**URL:** `https://scores.collegesailing.org/schools/tufts/`

**Available data:**
- School name
- Conference
- Current season roster link: `/schools/tufts/s26/roster/`
- Season selector (f08 through s26 - 18 years of data!)
- Number of regattas: **10**
- **REGATTA RESULTS VISIBLE** (8 completed, 1 upcoming)
  - Regatta name
  - Date
  - Place (e.g., "1/8", "5/8")
  - Status (Official/Pending)

**Key difference:** 🚨 **College pages SHOW results directly**
- HS requires visiting individual sailor pages
- College displays team results on school page
- This is a BETTER structure for scraping!

---

## 3. Roster Page Structure

### High School
**URL:** `https://scores.hssailing.org/schools/annapolis/s26/roster/`

**Pattern:** Links to individual sailors
```html
<a href="/sailors/john-smith-12345/">John Smith</a>
```

**Current scraper:** ✅ Works (roster_scraper.py lines 37-50)
- Finds all `<a>` tags with `/sailors/` in href
- Extracts sailor name from link text

---

### College
**URL:** `https://scores.collegesailing.org/schools/tufts/s26/roster/`

**Expected pattern:** Same as HS (needs verification)
- Links to individual sailors
- Same `/sailors/` URL pattern

**Compatibility:** ✅ Likely works as-is

---

## 4. Database Schema Compatibility

### Current School model (models.py:296-322)
```python
class School(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(300), nullable=False, index=True)
    district = db.Column(db.String(200))  # ← Can store conference
    source = db.Column(db.String(20), nullable=False)  # 'hs' or 'college'
    url_slug = db.Column(db.String(200), nullable=False)
    full_url = db.Column(db.String(500))
    created_at = db.Column(db.DateTime)
    updated_at = db.Column(db.DateTime)
```

**Required changes:** 🔧 Minor
- `district` column can store conference name (already exists)
- Consider adding optional `mascot` column (VARCHAR(100))
- Consider adding optional `city` and `state` columns

**Recommendation:**
```python
# Add these optional columns
mascot = db.Column(db.String(100))  # "Tufts", "Crimson", etc.
city = db.Column(db.String(100))    # "Medford", can be null
state = db.Column(db.String(2))     # "MA", can be null
```

---

## 5. Implementation Strategy

### Option A: Modify existing scrapers (✅ RECOMMENDED)
```python
# school_scraper.py
def scrape_schools(base_url="https://scores.hssailing.org/schools/"):
    # Add base_url parameter
    # Rest stays the same!

def scrape_college_schools():
    return scrape_schools("https://scores.collegesailing.org/schools/")
```

### Option B: Create separate college_scraper.py
- Copy HS scraper
- Change URLs
- Add mascot extraction
- More code duplication

**Recommendation:** Use Option A - parameterize existing code

---

## 6. Key Differences Summary

| Feature | High School | College | Compatible? |
|---------|-------------|---------|-------------|
| **Index page org** | Districts (H3) | Conferences (H3) | ✅ Yes |
| **School list** | Table | Table | ✅ Yes |
| **URL pattern** | `/schools/{slug}/` | `/schools/{slug}/` | ✅ Yes |
| **Mascot data** | Image (optional) | Text in 1st column | 🔧 Minor change |
| **City/State** | Always present | Sometimes empty | 🔧 Handle nulls |
| **Results location** | Individual sailor pages | School page! | 🚨 Major difference |
| **Roster format** | `/schools/{slug}/{season}/roster/` | Same | ✅ Yes |
| **Sailor links** | `/sailors/{name}/` | Likely same | ✅ Probably |

---

## 7. Recommended Implementation Plan

### Phase 1: Scrape College Schools (EASY)
1. Add `mascot`, `city`, `state` columns to School model
2. Modify `school_scraper.py`:
   ```python
   def scrape_schools(source='hs'):
       if source == 'hs':
           url = "https://scores.hssailing.org/schools/"
       else:
           url = "https://scores.collegesailing.org/schools/"

       # ... existing logic ...

       # For college, extract mascot from 1st <td>
       if source == 'college':
           mascot_cell = row.find('td')
           mascot = mascot_cell.get_text(strip=True) if mascot_cell else None
   ```

3. Test on college URL
4. Store 197 schools in database

### Phase 2: Scrape College Rosters (EASY)
1. Verify roster page structure matches HS
2. Change URL in `roster_scraper.py` to use `collegesailing.org`
3. Test on 2-3 schools

### Phase 3: Scrape College Results (DIFFERENT APPROACH)
**Option A:** Scrape from school page (FASTER)
- Regatta results are already visible on school page!
- No need to visit individual sailor pages
- Parse the regatta table directly

**Option B:** Scrape from individual sailors (HS approach)
- Visit each sailor page
- Slower but gets individual results
- Same as HS scraper

**Recommendation:** Use Option A - it's faster and team-level results are more relevant for college sailing

### Phase 4: Database Schema
```sql
-- Migration needed:
ALTER TABLE schools ADD COLUMN mascot VARCHAR(100);
ALTER TABLE schools ADD COLUMN city VARCHAR(100);
ALTER TABLE schools ADD COLUMN state VARCHAR(2);

-- Rename "district" to be more generic?
-- Or just document that district=conference for college
```

---

## 8. Code Changes Required

### Minimal changes (if using existing HS pattern):
```python
# school_scraper.py - Line 11
- url = "https://scores.hssailing.org/schools/"
+ url = "https://scores.collegesailing.org/schools/"

# Line 30 - Change variable name
- district_name = district_header.get_text(strip=True)
+ conference_name = district_header.get_text(strip=True)

# Line 51 - Add mascot extraction
+ mascot_cell = schools_container.find('td')  # First TD in row
+ mascot = mascot_cell.get_text(strip=True) if mascot_cell else None

# Line 50-55 - Update dict
  schools_data.append({
-     "District": district_name,
+     "Conference": conference_name,
+     "Mascot": mascot,
      "School_Name": school_name,
      "URL_Slug": url_slug,
+     "City": city_text,  # May be empty
+     "State": state_text,  # May be empty
      "Full_URL": full_url
  })
```

---

## 9. Testing Checklist

- [ ] Fetch college schools page
- [ ] Parse all 8 conferences
- [ ] Extract all 197 schools
- [ ] Verify URL slugs are correct
- [ ] Test with schools that have empty city/state
- [ ] Store in database with source='college'
- [ ] Fetch 3 sample school pages
- [ ] Verify roster links work
- [ ] Check if sailor links match HS pattern
- [ ] Parse regatta results from school page
- [ ] Store college results in database

---

## 10. Conclusion

✅ **YES, the HS scraper pattern works for college with minor modifications:**

1. **Same HTML structure** (H3 + tables)
2. **Same URL patterns** (`/schools/{slug}/`)
3. **Same roster approach** (likely)
4. **Better results access** (on school page, not sailor page)

**Estimated effort:**
- School scraping: 30 minutes
- Roster scraping: 15 minutes (verify only)
- Results scraping: 1 hour (different approach)
- Database migration: 15 minutes
- Testing: 1 hour

**Total: ~3 hours** to have a working college scraper
