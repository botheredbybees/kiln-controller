# Worked Example: Adding a Delta Time Column to the Profile Editor

This guide walks through a complete feature implementation from start to finish, demonstrating the development workflow for the kiln-controller web interface. We'll add a "Time Increment" column to the schedule editor that shows the time elapsed between each profile point, making it easier to understand and modify firing schedules.

## The Problem

Currently, users must enter "Target Time" as a cumulative total from the start of the firing schedule. For example, a schedule might have target times of 0, 5, 125, 239, and 414 minutes. If you want to change the ramp time from 120 minutes to 150 minutes, you must manually recalculate all subsequent times (239 becomes 269, 414 becomes 444). This is mentally taxing and violates the principle of "don't make me think."

Traditional PID controllers allow users to think in terms of incremental time periods: "Heat for 5 minutes, then for 120 minutes, then for 114 minutes, then for 185 minutes." This is more intuitive because each segment is independent.

## The Solution

We'll add a new "Time Increment" column that displays and accepts the time difference between consecutive profile points. Users can either:

1. Edit the cumulative "Target Time" (existing behavior)
2. Edit the "Time Increment" which will automatically recalculate cumulative times for all subsequent points

## Step 1: Setting Up Your Development Branch

Before making any code changes, create a dedicated branch for this feature.

```bash
# Navigate to your kiln-controller repository
cd ~/kiln-controller

# Make sure you're on the main branch and up to date
git checkout main
git pull origin main

# Create and switch to a new feature branch
git checkout -b feature/delta-time-column
```

This isolates your changes from the main codebase and makes it easy to create a pull request later.

## Step 2: Understanding the Current Implementation

Before modifying code, let's understand how the profile table currently works.

### Current Table Structure

The `updateProfileTable()` function in `public/assets/js/picoreflow.js` (around line 95) generates an HTML table with these columns:

1. **#** - Row number
2. **Target Time** - Cumulative time from start (editable)
3. **Target Temperature** - Temperature to reach (editable)
4. **Slope** - Calculated heating/cooling rate (read-only)

### Data Structure

Profile data is stored in `graph.profile.data` as an array of `[time, temperature]` pairs:

```javascript
// Example profile data
graph.profile.data = [
  [0, 120],      // At 0 minutes, target 120°C
  [300, 200],    // At 5 minutes (300 seconds), target 200°C
  [7500, 200],   // At 125 minutes (7500 seconds), target 200°C
  [14340, 600],  // At 239 minutes, target 600°C
  [24840, 1300]  // At 414 minutes, target 1300°C
];
```

Note that times are stored in seconds, but displayed in minutes or hours based on `time_scale_profile` configuration.

## Step 3: Implementing the Delta Time Column

Now we'll modify `updateProfileTable()` to add the new column and make it interactive.

### 3.1 Modify the Table Header

Find the line that creates the table header (around line 100) and add a new column:

```javascript
// OLD CODE:
html += '<tr><th style="width: 50px">#</th><th>Target Time in ' + time_scale_long+ '</th><th>Target Temperature in °'+temp_scale_display+'</th><th>Slope in &deg;'+temp_scale_display+'/'+time_scale_slope+'</th><th></th></tr>';

// NEW CODE:
html += '<tr><th style="width: 50px">#</th><th>Target Time in ' + time_scale_long+ '</th><th>Time Increment in ' + time_scale_long+ '</th><th>Target Temperature in °'+temp_scale_display+'</th><th>Slope in &deg;'+temp_scale_display+'/'+time_scale_slope+'</th><th></th></tr>';
```

### 3.2 Calculate and Display Delta Time

Inside the for loop that generates table rows, add logic to calculate the time increment:

```javascript
for(var i=0; i<graph.profile.data.length;i++)
{
    // Calculate delta time (time since previous point)
    var deltaTime = 0;
    if (i > 0) {
        deltaTime = graph.profile.data[i][0] - graph.profile.data[i-1][0];
    }
    
    // Existing slope calculation
    if (i>=1) dps =  ((graph.profile.data[i][1]-graph.profile.data[i-1][1])/(graph.profile.data[i][0]-graph.profile.data[i-1][0]) * 10) / 10;
    if (dps  > 0) { slope = "up";     color="rgba(206, 5, 5, 1)"; } else
    if (dps  < 0) { slope = "down";   color="rgba(23, 108, 204, 1)"; dps *= -1; } else
    if (dps == 0) { slope = "right";  color="grey"; }

    html += '<tr><td><h4>' + (i+1) + '</h4></td>';
    
    // Target Time (cumulative) - existing column
    html += '<td><input type="text" class="form-control" id="profiletable-0-'+i+'" value="'+ timeProfileFormatter(graph.profile.data[i][0],true) + '" style="width: 60px" /></td>';
    
    // NEW: Time Increment (delta) column
    var deltaTimeFormatted = timeProfileFormatter(deltaTime, true);
    var deltaTimeClass = (i === 0) ? 'readonly' : '';
    html += '<td><input type="text" class="form-control" id="profiletable-delta-'+i+'" value="'+ deltaTimeFormatted + '" style="width: 60px" '+ (i === 0 ? 'readonly' : '') +' /></td>';
    
    // Target Temperature - existing column
    html += '<td><input type="text" class="form-control" id="profiletable-1-'+i+'" value="'+ graph.profile.data[i][1] + '" style="width: 60px" /></td>';
    
    // Slope - existing column
    html += '<td><div class="input-group"><span class="glyphicon glyphicon-circle-arrow-' + slope + ' input-group-addon ds-trend" style="background: '+color+'"></span><input type="text" class="form-control ds-input" readonly value="' + formatDPS(dps) + '" style="width: 100px" /></div></td>';
    html += '<td>&nbsp;</td></tr>';
}
```

Note: The first row's delta time is always 0 and is read-only since there's no previous point.

### 3.3 Handle Delta Time Input Changes

Modify the existing change handler to process edits to the delta time column. Replace the existing `$(".form-control").change()` handler with this enhanced version:

```javascript
//Link table to graph
$(".form-control").change(function(e)
{
    var id = $(this)[0].id;
    var value = parseInt($(this)[0].value);
    var fields = id.split("-");
    
    if (graph.profile.data.length > 0) {
        
        // Handle delta time changes
        if (fields[1] === "delta") {
            var row = parseInt(fields[2]);
            
            // Can't edit delta time on first row
            if (row === 0) return;
            
            // Convert display value to seconds
            var deltaSeconds = timeProfileFormatter(value, false);
            
            // Calculate new cumulative time for this point
            var newCumulativeTime = graph.profile.data[row-1][0] + deltaSeconds;
            
            // Calculate the time shift (difference from old time)
            var timeShift = newCumulativeTime - graph.profile.data[row][0];
            
            // Update this point and all subsequent points
            for (var i = row; i < graph.profile.data.length; i++) {
                graph.profile.data[i][0] = graph.profile.data[i][0] + timeShift;
            }
        }
        // Handle cumulative time changes (existing behavior)
        else if (fields[1] === "0") {
            var row = parseInt(fields[2]);
            var newTime = timeProfileFormatter(value, false);
            
            // Calculate the time shift
            var timeShift = newTime - graph.profile.data[row][0];
            
            // Update this point and all subsequent points
            for (var i = row; i < graph.profile.data.length; i++) {
                graph.profile.data[i][0] = graph.profile.data[i][0] + timeShift;
            }
        }
        // Handle temperature changes (existing behavior)
        else if (fields[1] === "1") {
            var row = parseInt(fields[2]);
            graph.profile.data[row][1] = value;
        }

        graph.plot = $.plot("#graph_container", [ graph.profile, graph.live ], getOptions());
    }
    updateProfileTable();
});
```

This handler now:
- Detects which column was edited (delta, cumulative time, or temperature)
- For delta time edits: calculates the new cumulative time and shifts all subsequent points
- For cumulative time edits: shifts all subsequent points to maintain their relative spacing
- For temperature edits: updates only that specific point (unchanged behavior)

## Step 4: Testing Your Changes

Thorough testing ensures the feature works correctly before sharing it with others.

### 4.1 Local Testing in Simulation Mode

First, test without connecting to real hardware:

```bash
# Edit config.py and set:
simulate = True

# Run the controller
python kiln-controller.py
```

Open your browser to `http://localhost:8081` (or your configured port).

### 4.2 Test Cases

Work through these scenarios systematically:

**Test 1: Verify Delta Time Display**
1. Click "Edit" on any existing profile
2. Click the table icon to show the schedule table
3. Verify that the "Time Increment" column shows:
   - 0 for the first row
   - The difference between consecutive cumulative times for other rows
4. Verify that values are displayed in the correct time unit (minutes or hours based on config)

**Test 2: Edit Delta Time (Middle of Schedule)**
1. Change a delta time value in row 3 from (for example) 120 to 150
2. Verify that:
   - The cumulative time for row 3 increases by 30
   - All subsequent rows' cumulative times also increase by 30
   - The delta times for other rows remain unchanged
   - The graph updates to reflect the new schedule

**Test 3: Edit Delta Time (Last Row)**
1. Change the delta time on the last row
2. Verify that:
   - Only that row's cumulative time changes
   - The graph extends or contracts appropriately

**Test 4: Edit Cumulative Time**
1. Edit a "Target Time" value directly (existing feature)
2. Verify that:
   - Delta times recalculate correctly
   - Subsequent cumulative times shift appropriately
   - The feature still works as it did before

**Test 5: Add and Delete Points**
1. Click the "+" button to add a new point
2. Verify the new point shows correct delta time
3. Click the "-" button to delete a point
4. Verify remaining delta times are correct

**Test 6: Create New Profile**
1. Click "New Profile" button
2. Add several points
3. Edit delta times to create your desired schedule
4. Save the profile
5. Reload and verify the schedule displays correctly

**Test 7: Save and Reload**
1. Edit a profile using delta times
2. Save it
3. Refresh the browser page
4. Load the profile again
5. Verify all times are preserved correctly

### 4.3 Browser Console Testing

Open the browser console (F12) and verify:
- No JavaScript errors appear
- WebSocket messages show correct data when saving profiles
- The `graph.profile.data` array contains expected time values

You can inspect the data structure directly in the console:

```javascript
// View current profile data
console.log(graph.profile.data);

// Should show something like:
// [[0, 120], [300, 200], [7500, 200], [14340, 600], [24840, 1300]]
```

### 4.4 Edge Cases

Test these unusual scenarios:

- Enter "0" as a delta time (should work - creates a hold at temperature)
- Enter negative numbers (should be prevented or handled gracefully)
- Enter very large numbers (should work but verify graph rendering)
- Edit delta time on first row (should be prevented - field is readonly)

## Step 5: Committing Your Changes

Once testing is complete, commit your work with a clear, descriptive message.

```bash
# Check what files have changed
git status

# Review your changes
git diff public/assets/js/picoreflow.js

# Stage the modified file
git add public/assets/js/picoreflow.js

# Commit with a descriptive message
git commit -m "Add Time Increment column to profile editor

The profile editor now includes a Time Increment column that shows
the time elapsed between consecutive profile points. Users can edit
this column to modify schedules more intuitively without manually
recalculating cumulative times.

Features:
- Display delta time between profile points
- Edit delta time to automatically adjust cumulative times
- All subsequent points shift when a delta time is modified
- First row delta time is read-only (always 0)
- Works with existing cumulative time editing
- Supports all time scale configurations (seconds/minutes/hours)

Testing:
- Verified delta time calculations
- Tested editing middle and end points
- Confirmed backward compatibility with cumulative time editing
- Validated profile save/load functionality"

# Push your branch to GitHub
git push origin feature/delta-time-column
```

## Step 6: Creating a Pull Request

Now you'll propose your changes to be merged into the main project.

### 6.1 Push to Your Fork

If you haven't already pushed your branch:

```bash
git push origin feature/delta-time-column
```

### 6.2 Create the Pull Request on GitHub

1. Navigate to your fork on GitHub: `https://github.com/YOUR-USERNAME/kiln-controller`
2. Click the "Pull requests" tab
3. Click "New pull request"
4. Set the base repository to `jbruce12000/kiln-controller` and base branch to `main`
5. Set the compare repository to your fork and compare branch to `feature/delta-time-column`
6. Review the changes shown in the diff
7. Click "Create pull request"

### 6.3 Write a Descriptive Pull Request

Provide context to help reviewers understand your changes:

**Title:**
```
Add Time Increment column to profile editor for easier schedule editing
```

**Description:**
```markdown
## Problem
Users currently must enter cumulative target times in the profile editor. When adjusting 
a firing schedule, changing one time point requires manually recalculating all subsequent 
times, which is error-prone and mentally taxing.

## Solution
This PR adds a "Time Increment" column to the profile editor that displays and accepts 
the time difference between consecutive profile points.

## Changes
- Added Time Increment column to profile table
- Implemented automatic recalculation of cumulative times when delta times are edited
- Maintained backward compatibility with existing cumulative time editing
- Made first row's delta time read-only (always 0)

## Example
Instead of entering cumulative times like: 0, 5, 125, 239, 414
Users can now think in increments: 0, 5, 120, 114, 185

Changing a ramp time from 120 to 150 minutes automatically adjusts all subsequent times.

## Testing
- ✓ Verified delta time calculations are correct
- ✓ Tested editing delta times in various positions
- ✓ Confirmed cumulative time editing still works
- ✓ Validated profile save/load preserves schedules
- ✓ Tested with simulation mode
- ✓ Checked browser console for errors

## Screenshots
(You could add screenshots showing before/after the table structure)

## Related Issues
(If there's an existing issue, reference it here: "Fixes #123")
```

### 6.4 Respond to Review Feedback

Maintainers may request changes or ask questions. To update your PR:

```bash
# Make requested changes to the code
# ...

# Commit the updates
git add public/assets/js/picoreflow.js
git commit -m "Address review feedback: improve delta time validation"

# Push to the same branch
git push origin feature/delta-time-column
```

The pull request will automatically update with your new commits.

## Step 7: After the PR is Merged

Once your pull request is accepted and merged:

```bash
# Switch back to main branch
git checkout main

# Pull the latest changes (including your merged feature)
git pull upstream main  # or origin main if working on the main repo

# Delete your local feature branch (optional, for cleanup)
git branch -d feature/delta-time-column

# Delete the remote branch (optional)
git push origin --delete feature/delta-time-column
```

## Advanced Enhancements

Once the basic feature is working, consider these improvements:

### Input Validation

Add validation to prevent invalid time values:

```javascript
if (fields[1] === "delta") {
    var row = parseInt(fields[2]);
    if (row === 0) return;
    
    // Validate input
    if (isNaN(value) || value < 0) {
        $.bootstrapGrowl("Time increment must be a positive number", {
            type: 'error',
            delay: 3000
        });
        updateProfileTable(); // Reset to previous value
        return;
    }
    
    // ... rest of handler
}
```

### Visual Feedback

Add CSS styling to differentiate the delta time column:

```javascript
// In the table generation
html += '<td><input type="text" class="form-control delta-time-input" id="profiletable-delta-'+i+'" ...';
```

Then add custom CSS in `public/assets/css/picoreflow.css`:

```css
.delta-time-input {
    background-color: #f0f8ff;
    border-left: 3px solid #4682b4;
}
```

### Keyboard Shortcuts

Add arrow key navigation between table cells for faster editing.

## Lessons Learned

This worked example demonstrates several important practices:

1. **Understand before modifying** - We analyzed the existing code structure first
2. **Incremental development** - Changes were added step by step
3. **Backward compatibility** - The existing cumulative time editing still works
4. **Comprehensive testing** - Multiple test cases ensure reliability
5. **Clear documentation** - Commit messages and PR descriptions explain the why and how
6. **Use feature branches** - Isolated changes from the main codebase

These principles apply to any feature development on the kiln-controller project.

## Getting Help

If you encounter issues:

- Check the browser console for JavaScript errors
- Review the [main development guide](web-interface-development.md)
- Ask questions in GitHub issues or discussions
- Compare your code to the working examples in the repository

## Next Steps

Now that you've completed a full feature implementation:

1. Try implementing another feature from the issues list
2. Improve an existing feature based on your own ideas
3. Help review pull requests from other contributors
4. Share your kiln-controller setup and experiences with the community
