# Contributing to Regatta Resume Builder

Thank you for your interest in contributing to Regatta Resume Builder! This document provides guidelines and instructions for contributing to the project.

## Code of Conduct

Please be respectful and professional in all interactions. We aim to maintain a welcoming and inclusive environment for all contributors.

## Getting Started

### Prerequisites

- Python 3.8 or higher
- Git
- Chrome/Chromium browser
- ChromeDriver

### Development Setup

1. Fork the repository on GitHub

2. Clone your fork:
```bash
git clone https://github.com/YOUR_USERNAME/regatta_resume.git
cd regatta_resume
```

3. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

4. Install dependencies:
```bash
cd regatta_resume
pip install -r requirements.txt
```

5. Create a `.env` file from the example:
```bash
cp ../.env.example ../.env
```

6. Run the application:
```bash
python app.py
```

## Development Workflow

### Creating a Branch

Always create a new branch for your work:

```bash
git checkout -b feature/your-feature-name
# or
git checkout -b fix/bug-description
```

Branch naming conventions:
- `feature/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation changes
- `refactor/` - Code refactoring
- `test/` - Adding or updating tests

### Making Changes

1. **Write Clean Code**
   - Follow PEP 8 style guidelines
   - Use meaningful variable and function names
   - Add docstrings to functions and classes
   - Keep functions focused and small

2. **Add Tests**
   - Write unit tests for new functionality
   - Ensure existing tests still pass
   - Aim for high test coverage

3. **Update Documentation**
   - Update README.md if needed
   - Add/update docstrings
   - Update comments where necessary

### Running Tests

Run the test suite before submitting:

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=regatta_resume

# Run specific test file
pytest tests/test_validators.py

# Run specific test
pytest tests/test_validators.py::TestValidateSailorName::test_valid_name
```

### Code Style

We follow PEP 8 guidelines. You can check your code with:

```bash
# Install flake8 if you haven't
pip install flake8

# Check code style
flake8 regatta_resume/
```

### Committing Changes

1. **Write Good Commit Messages**
   - Use present tense ("Add feature" not "Added feature")
   - First line should be 50 characters or less
   - Provide detailed description in body if needed
   - Reference issues when applicable

Example:
```
Add sailor name validation

- Implement comprehensive name validation
- Add tests for edge cases
- Update documentation

Fixes #123
```

2. **Commit Often**
   - Make small, logical commits
   - Each commit should represent a single logical change
   - Don't commit commented-out code or debug statements

### Pushing Changes

```bash
git push origin feature/your-feature-name
```

## Submitting a Pull Request

1. **Before Submitting**
   - [ ] All tests pass
   - [ ] Code follows style guidelines
   - [ ] Documentation is updated
   - [ ] Commit messages are clear
   - [ ] Branch is up to date with main

2. **Create Pull Request**
   - Go to GitHub and create a new pull request
   - Provide a clear title and description
   - Reference related issues
   - Add screenshots for UI changes

3. **PR Description Template**
```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
Describe how you tested your changes

## Checklist
- [ ] Tests pass
- [ ] Code follows style guidelines
- [ ] Documentation updated
- [ ] Self-review completed
```

## Development Guidelines

### Project Structure

```
regatta_resume/
├── app.py              # Main Flask application
├── Resume.py           # Clubspot scraper
├── scraper.py          # Techscore scraper
├── resume_pdf.py       # PDF generation
├── config.py           # Configuration management
├── validators.py       # Input validation
├── logger.py           # Logging utilities
├── templates/          # HTML templates
└── static/             # Static files
```

### Adding New Features

When adding a new feature:

1. **Plan First**
   - Discuss major changes in an issue first
   - Consider impact on existing functionality
   - Think about backwards compatibility

2. **Implementation**
   - Add configuration options to `config.py`
   - Add validation to `validators.py` if needed
   - Use the logger from `logger.py`
   - Add appropriate error handling

3. **Testing**
   - Add unit tests
   - Add integration tests if applicable
   - Test edge cases and error conditions

4. **Documentation**
   - Update README.md
   - Add docstrings
   - Update CHANGELOG.md (if exists)

### Fixing Bugs

1. **Reproduce the Bug**
   - Create a test that demonstrates the bug
   - Document steps to reproduce

2. **Fix**
   - Make the minimal change needed
   - Don't refactor unrelated code
   - Add comments explaining non-obvious fixes

3. **Verify**
   - Ensure the test now passes
   - Check for regressions
   - Test related functionality

## Specific Areas

### Adding Data Sources

To add a new sailing data source:

1. Create a scraper function in a new module or extend existing scrapers
2. Add configuration in `config.py`
3. Update the data processing pipeline in `app.py`
4. Add tests for the scraper
5. Update documentation

### Adding PDF Styles

To add a new PDF template style:

1. Create a new template in `templates/`
2. Add a PDF generation function in `resume_pdf.py`
3. Update the PDF selection page
4. Add a route in `app.py`
5. Test PDF generation

### Improving Validation

When adding new validation:

1. Add function to `validators.py`
2. Write comprehensive tests
3. Use validation in appropriate routes
4. Update error messages to be user-friendly

## Questions or Problems?

- Check existing issues on GitHub
- Create a new issue if your question isn't answered
- Be specific and provide examples
- Include error messages and stack traces

## Recognition

Contributors will be recognized in:
- README.md acknowledgments
- Git commit history
- Release notes (for significant contributions)

Thank you for contributing to Regatta Resume Builder!
