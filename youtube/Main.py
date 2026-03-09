from flask import Flask, render_template, request, redirect, url_for, session, flash
import os
import pyodbc
import assemblyai as aai
from datetime import datetime
from datetime import timedelta
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your_secret_key')

app.config['UPLOAD_FOLDER'] = 'static/video/'
app.config['SUBTITLE_FOLDER'] = 'static/subtitles/'
app.config['ALLOWED_EXTENSIONS'] = {'mp4', 'mkv', 'avi', 'mov'}

# Cấu hình kết nối SQL ServerUPLOAD_FOLDER
DB_CONFIG = {
    'server': os.getenv('DB_SERVER', r''),
    'database': os.getenv('DB_NAME', ''),
    'username': os.getenv('DB_USER', ''),
    'password': os.getenv('DB_PASSWORD', '')
}
aai.settings.api_key = ""
# Cấu hình API KEY cho AssemblyAI
aai.settings.api_key = os.getenv('ASSEMBLYAI_API_KEY', '')

def get_db_connection():
    conn_str = (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER={DB_CONFIG['server']};"
        f"DATABASE={DB_CONFIG['database']};"
        f"UID={DB_CONFIG['username']};"
        f"PWD={DB_CONFIG['password']}"
    )
    return pyodbc.connect(conn_str)

def row_to_dict(cursor, row):
    if row is None:
        return None
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# Tạo phụ đề bằng AssemblyAI
def create_subtitles(video_path, output_vtt_path):
    try:
        transcriber = aai.Transcriber()
        upload_url = aai.upload_file(video_path)
        print("Upload URL:", upload_url)  # debug

        transcript = transcriber.transcribe(
            upload_url,
            config=aai.TranscriptionConfig(language_code='vi')
        )

        print("Transcript ID:", transcript.id)  # debug
        transcript.wait_till_complete()

        if transcript.status == aai.TranscriptStatus.error:
            print(f"Lỗi AssemblyAI: {transcript.error}")
            return False

        with open(output_vtt_path, 'w', encoding='utf-8') as f:
            f.write(transcript.export_subtitles_vtt())
        print("Phụ đề đã được tạo:", output_vtt_path)  # debug

        return True
    except Exception as e:
        print(f"Lỗi tạo phụ đề: {e}")
        return False


# Route 1: Trang chủ
@app.route('/')
def index():
    page = int(request.args.get('page', 1))
    per_page = 8
    offset = (page - 1) * per_page

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM Video")
    total_videos = cursor.fetchone()[0]
    total_pages = (total_videos + per_page - 1) // per_page

    cursor.execute("""
        SELECT video_id, title, description, filename, subtitle_filename, views, user_id, upload_date
        FROM Video
        ORDER BY upload_date DESC
        LIMIT %s OFFSET %s
    """, (per_page, offset))  # Dùng %s cho tham số
    video_files = [row_to_dict(cursor, row) for row in cursor.fetchall()]
    conn.close()

    return render_template('index.html', video_files=video_files, page=page, total_pages=total_pages)


# Route 2: Xem video
@app.route('/watch/<int:video_id>', methods=['GET', 'POST'])
def watch(video_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Lấy thông tin video
    cursor.execute("SELECT * FROM Video WHERE video_id = %s", (video_id,))
    video = row_to_dict(cursor, cursor.fetchone())
    
    if not video:
        conn.close()
        return "Video không tồn tại", 404

    # Cập nhật lượt xem video
    cursor.execute("UPDATE Video SET views = views + 1 WHERE video_id = %s", (video_id,))
    conn.commit()

    # Lấy thông tin người dùng đang đăng nhập
    user = session.get('user')
    if user:
        user_id = user['user_id']
        email = user['email']
        watch_time = datetime.now()  # Lưu thời gian người dùng bắt đầu xem
        duration_watched = 0  # Bạn có thể thay đổi giá trị này dựa trên thời gian xem thực tế

        # Lưu lịch sử vào bảng History
        cursor.execute("""
            INSERT INTO watch_history (user_id, video_id, watch_time, duration_watched)
            VALUES (%s, %s, %s, %s)
        """, (user_id, video_id, watch_time, duration_watched))
        conn.commit()

    # Lấy bình luận của video
    cursor.execute("""
        SELECT c.comment_text, c.comment_date, u.Email
        FROM comments c
        JOIN users u ON c.user_id = u.UserID
        WHERE c.video_id = %s
        ORDER BY c.comment_date DESC
    """, (video_id,))
    comments = [row_to_dict(cursor, row) for row in cursor.fetchall()]
    
    conn.close()
    return render_template('watch.html', video=video, comments=comments)

@app.route('/history')
def history():
    user = session.get('user')
    if 'user' not in session:
        flash("Bạn cần đăng nhập để xem lịch sử!", "warning")
        return redirect(url_for('login'))

    user_id = session['user']['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT h.watch_time, h.duration_watched, v.title, v.filename, v.video_id, u.Email
        FROM watch_history h
        JOIN Video v ON h.video_id = v.video_id
        JOIN Users u ON h.user_id = u.UserID
        WHERE h.user_id = %s
        ORDER BY h.watch_time DESC
    """, (user_id,))
    
    history = [row_to_dict(cursor, row) for row in cursor.fetchall()]
    conn.close()

    return render_template('history.html', history=history)

# Route 3: Upload video
@app.route('/upload', methods=['POST'])
def upload_file():
    if 'user' not in session:
        flash("Bạn cần đăng nhập để tải video!", "warning")
        return redirect(url_for('login'))

    if 'video' not in request.files or 'title' not in request.form:
        flash("Thiếu thông tin!", "danger")
        return redirect(url_for('index'))

    video = request.files['video']
    title = request.form['title']
    description = request.form.get('description', '')

    if video and allowed_file(video.filename):
        original_filename = secure_filename(video.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], original_filename)
        video.save(filepath)

        converted_filename = original_filename
        subtitle_filename = os.path.splitext(original_filename)[0] + '.vtt'
        subtitle_path = os.path.join(app.config['SUBTITLE_FOLDER'], subtitle_filename)

        # Gọi AssemblyAI để tạo phụ đề
        try:
            transcriber = aai.Transcriber()
            transcript = transcriber.transcribe(filepath)
            subtitles = transcript.export_subtitles_vtt()
            with open(subtitle_path, "w", encoding="utf-8") as f:
                f.write(subtitles)
            success = True
        except Exception as e:
            print("Lỗi tạo phụ đề với AssemblyAI:", e)
            success = False

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO Video (user_id, title, description, filename, views, upload_date, converted_filename, subtitle_filename)
            VALUES (%s, %s, %s, %s, 0, NOW(), %s, %s)
        """, (
            session['user']['user_id'],
            title,
            description,
            original_filename,
            converted_filename,
            subtitle_filename if success else None
        ))
        conn.commit()
        conn.close()

        flash("Tải video thành công!" if success else "Tải thành công nhưng lỗi khi tạo phụ đề!", "success" if success else "warning")
        return redirect(url_for('index'))

    flash("File không hợp lệ!", "danger")
    return redirect(url_for('index'))


# Route 4: Đăng ký
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO Users (Email, PasswordHash, CreatedAt, IsActive) VALUES (%s, %s, NOW(), 1)", (email, hashed_password))
            conn.commit()
            flash("Đăng ký thành công!", "success")
            return redirect(url_for('login'))
        except Exception as e:
            conn.rollback()
            flash(f"Lỗi đăng ký: {e}", "danger")
        finally:
            conn.close()
    return render_template('register.html')


# Route 5: Đăng nhập
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT UserID, PasswordHash FROM Users WHERE Email = %s", (email,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user[1], password):
            session['user'] = {'user_id': user[0], 'email': email}
            flash("Đăng nhập thành công!", "success")
            return redirect(url_for('index'))
        else:
            flash("Sai email hoặc mật khẩu!", "danger")
    return render_template('login.html')


# Route 6: Đăng xuất
@app.route('/logout')
def logout():
    session.pop('user', None)
    flash("Bạn đã đăng xuất.", "info")
    return redirect(url_for('index'))


# Route 7: Tìm kiếm video
@app.route('/search')
def search():
    query = request.args.get('query', '').strip().lower()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT video_id, title, description, filename, subtitle_filename, views
        FROM Video
        WHERE LOWER(title) LIKE %s
    """, ('%' + query + '%',))
    filtered_videos = [row_to_dict(cursor, row) for row in cursor.fetchall()]
    conn.close()
    return render_template('index.html', video_files=filtered_videos, search_query=query)


# Route 8: Thích video
@app.route('/like/<int:video_id>', methods=['POST'])
def like_video(video_id):
    if 'user' not in session:
        flash("Bạn cần đăng nhập để thích video!", "warning")
        return redirect(url_for('login'))

    user = session.get('user')
    if not user or 'user_id' not in user:
        return redirect(url_for('login'))
    user_id = user['user_id']

    like_status = int(request.form['like_status'])

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT like_status FROM video_likes WHERE user_id = %s AND video_id = %s", (user_id, video_id))
    existing_like = cursor.fetchone()

    if existing_like:
        if existing_like[0] == like_status:
            cursor.execute("DELETE FROM video_likes WHERE user_id = %s AND video_id = %s", (user_id, video_id))
        else:
            cursor.execute("UPDATE video_likes SET like_status = %s WHERE user_id = %s AND video_id = %s",
                           (like_status, user_id, video_id))
    else:
        cursor.execute("INSERT INTO video_likes (user_id, video_id, like_status) VALUES (%s, %s, %s)",
                       (user_id, video_id, like_status))

    conn.commit()
    conn.close()
    return redirect(url_for('watch', video_id=video_id))


# Route 9: Bình luận video
@app.route('/comment/<int:video_id>', methods=['POST'])
def comment_video(video_id):
    if 'user' not in session:
        flash("Bạn cần đăng nhập để bình luận!", "warning")
        return redirect(url_for('login'))

    user_id = session['user']['user_id']
    comment_text = request.form['comment']

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO comments (user_id, video_id, comment_text, comment_date) VALUES (%s, %s, %s, NOW())",
                   (user_id, video_id, comment_text))
    conn.commit()
    conn.close()
    return redirect(url_for('watch', video_id=video_id))


# Route 10: Đăng ký kênh
@app.route('/subscribe/<int:channel_id>', methods=['POST'])
def subscribe(channel_id):
    if 'user' not in session:
        flash("Bạn cần đăng nhập để đăng ký kênh!", "warning")
        return redirect(url_for('login'))

    user_id = session['user']['user_id']
    if user_id == channel_id:
        flash("Bạn không thể đăng ký chính mình!", "danger")
        return redirect(request.referrer or url_for('index'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM subscriptions WHERE subscriber_id = %s AND channel_id = %s", (user_id, channel_id))
    existing = cursor.fetchone()

    if existing:
        cursor.execute("DELETE FROM subscriptions WHERE subscriber_id = %s AND channel_id = %s", (user_id, channel_id))
        flash("Bạn đã hủy đăng ký.", "info")
    else:
        cursor.execute("INSERT INTO subscriptions (subscriber_id, channel_id) VALUES (%s, %s)", (user_id, channel_id))
        flash("Đăng ký thành công!", "success")

    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('index'))


@app.route('/my_videos')
def my_videos():
    user = session.get('user')
    if 'user' not in session:
        flash("Bạn cần đăng nhập để xem trang này.", "warning")
        return redirect(url_for('login'))

    user_id = session['user']['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()

    # Cập nhật câu lệnh SQL với %s thay vì ?
    cursor.execute("""
        SELECT video_id, title, description, filename, subtitle_filename, views, upload_date
        FROM Video
        WHERE user_id = %s
        ORDER BY upload_date DESC
    """, (user_id,))

    # Lấy dữ liệu và chuyển đổi thành danh sách các từ điển
    videos = [row_to_dict(cursor, row) for row in cursor.fetchall()]
    conn.close()

    return render_template('my_videos.html', videos=videos)


@app.route('/delete_video/<int:video_id>', methods=['POST'])
def delete_video(video_id):
    if 'user' not in session:
        flash("Bạn cần đăng nhập.", "warning")
        return redirect(url_for('login'))

    user_id = session['user']['user_id']
    conn = get_db_connection()
    cursor = conn.cursor()

    # Cập nhật câu lệnh SQL với %s thay vì ?
    cursor.execute("SELECT filename, subtitle_filename, user_id FROM Video WHERE video_id = %s", (video_id,))
    video = cursor.fetchone()

    if not video:
        flash("Video không tồn tại.")
        return redirect(url_for('my_videos'))

    if video.user_id != user_id:
        flash("Bạn không có quyền xoá video này.")
        return redirect(url_for('my_videos'))

    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], video[0])  # video[0] là filename
        if video[1]:  # video[1] là subtitle_filename
            subpath = os.path.join(app.config['SUBTITLE_FOLDER'], video[1])
            if os.path.exists(subpath):
                os.remove(subpath)
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception as e:
        flash(f"Lỗi khi xoá file: {e}", "danger")
        return redirect(url_for('my_videos'))

    # Cập nhật câu lệnh SQL với %s thay vì ?
    cursor.execute("DELETE FROM Video WHERE video_id = %s", (video_id,))
    conn.commit()
    conn.close()

    flash("Video đã được xoá.", "success")
    return redirect(url_for('my_videos'))
if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['SUBTITLE_FOLDER'], exist_ok=True)
    app.run(debug=True)
