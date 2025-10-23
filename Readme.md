Run the following commands to clone

<pre>
git clone https://github.com/sahil-gupta00790/Shubh-Tasks
cd Shubh-Tasks

</pre>

Then to run it, use the following commands in powersheel terminal 1:
<pre>
cd FaceRecognitionDesktop/Desktop
</pre>
<pre>
javac -cp "lib/opencv-4120.jar" src/main/java/com/example/Shubh/App.java -d target/classes
</pre>
<pre>
java "-Djava.library.path=./lib" -cp "lib/opencv-4120.jar;target/classes" com.example.Shubh.App
</pre>


Next, run the following commands in terminal 2(powershell):
<pre>
cd FaceRecognitionServer
</pre>

<pre>
mvn exec:java "-Dexec.mainClass=com.example.Shubh.LocalServer"
</pre>

